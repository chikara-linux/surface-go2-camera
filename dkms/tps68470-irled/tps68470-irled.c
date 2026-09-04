// SPDX-License-Identifier: GPL-2.0
/*
 * TPS68470 IR illuminator (flash/torch) as a LED class device
 *
 * Surface Go 2 などの Intel IPU3 機は、IR カメラ (OV7251) の赤外線照明を
 * TPS68470 PMIC 内蔵のフラッシュ LED ドライバで駆動している。
 * mainline の include/linux/mfd/tps68470.h はクロック・GPIO・レギュレータ
 * しか定義しておらず、0x28-0x3A のフラッシュ制御ブロックが欠落している。
 * そのため Linux には照明を点ける手段が無い。
 *
 * レジスタ手順は Windows のドライバ iactrllogic64.sys の
 * tps68470::Tps68470Flash::TorchPowerOn を逆アセンブルして復元した。
 *
 * 既存の int3472-tps68470 が作った regmap を共有する。regmap のロックで
 * アクセスが直列化されるため、ユーザー空間から i2c を直接叩く方式と違い
 * PMIC の状態を壊さない。
 *
 * 上流に出すなら MFD にセルを足して drivers/leds/ に置くのが正しい形。
 * これはその前段としてのローカル実装。
 */

#include <linux/i2c.h>
#include <linux/leds.h>
#include <linux/module.h>
#include <linux/regmap.h>

#define TPS68470_REG_ENABLE	0x29	/* 1 で照明ブロックを有効化 */
#define TPS68470_REG_2C		0x2c
#define TPS68470_REG_CURRENT	0x2d	/* 電流。下位3ビットのみ有効 */
#define TPS68470_REG_2E		0x2e
#define TPS68470_REG_2F		0x2f	/* ストロボ駆動時はこちらに電流 */
#define TPS68470_REG_TIMEOUT	0x30	/* 自動消灯までの時間。3ビット */
#define TPS68470_REG_LED1_CUR	0x34
#define TPS68470_REG_LED2_CUR	0x35
#define TPS68470_REG_CTRL	0x36

/*
 * 0x36 の値。bit4(0x10) を立てると点灯しない（別の LED 出力が選ばれる）。
 * 0x45 で準備し、0x65 で点灯する 2 段書き込みが必要。
 */
#define TPS68470_CTRL_ARM	0x45
#define TPS68470_CTRL_ON	0x65
#define TPS68470_CTRL_OFF	0x00

#define TPS68470_IRLED_MAX	7	/* 電流は 3 ビット */

/*
 * フラッシュ LED ドライバは安全のため一定時間で自動消灯する。
 * 既定値 0 は最短で、連続点灯させると 1〜2 秒で消えてしまう
 * （顔認証が数百ミリ秒で終わるため当初は気づけなかった）。
 * Windows の FlashWithStrobeInitialize も 0x30 に値を書いている。
 */
#define TPS68470_TIMEOUT_MAX	0x07

struct tps68470_irled {
	struct led_classdev cdev;
	struct regmap *regmap;
	struct device *dev;
};

static int tps68470_irled_set(struct led_classdev *cdev,
			      enum led_brightness brightness)
{
	struct tps68470_irled *led =
		container_of(cdev, struct tps68470_irled, cdev);
	unsigned int cur = brightness & TPS68470_IRLED_MAX;
	int ret;

	if (!cur) {
		ret = regmap_write(led->regmap, TPS68470_REG_CTRL,
				   TPS68470_CTRL_OFF);
		if (ret)
			return ret;
		regmap_write(led->regmap, TPS68470_REG_ENABLE, 0x00);
		regmap_write(led->regmap, TPS68470_REG_CURRENT, 0x00);
		regmap_write(led->regmap, TPS68470_REG_LED1_CUR, 0x00);
		regmap_write(led->regmap, TPS68470_REG_LED2_CUR, 0x00);
		regmap_write(led->regmap, TPS68470_REG_TIMEOUT, 0x00);
		return 0;
	}

	/*
	 * 点灯したまま電流を書き換えても反映されない。消灯 → 電流設定 →
	 * 点灯 の順でないとラッチされないため、常に一度落としてから組み直す。
	 */
	regmap_write(led->regmap, TPS68470_REG_CTRL, TPS68470_CTRL_OFF);

	ret = regmap_write(led->regmap, TPS68470_REG_ENABLE, 0x01);
	if (ret)
		return ret;
	regmap_write(led->regmap, TPS68470_REG_2C, 0x00);
	regmap_write(led->regmap, TPS68470_REG_CURRENT, cur);
	regmap_write(led->regmap, TPS68470_REG_2F, 0x00);
	regmap_write(led->regmap, TPS68470_REG_2E, 0x00);
	regmap_write(led->regmap, TPS68470_REG_TIMEOUT, TPS68470_TIMEOUT_MAX);
	regmap_write(led->regmap, TPS68470_REG_LED1_CUR, cur);
	regmap_write(led->regmap, TPS68470_REG_LED2_CUR, cur);
	regmap_write(led->regmap, TPS68470_REG_CTRL, TPS68470_CTRL_ARM);
	return regmap_write(led->regmap, TPS68470_REG_CTRL, TPS68470_CTRL_ON);
}

static struct tps68470_irled *g_led;

static int tps68470_match(struct device *dev, const void *data)
{
	return dev->driver && !strcmp(dev->driver->name, "int3472-tps68470");
}

static int __init tps68470_irled_init(void)
{
	struct device *dev;
	struct regmap *regmap;
	unsigned int val;
	int ret;

	dev = bus_find_device(&i2c_bus_type, NULL, NULL, tps68470_match);
	if (!dev) {
		pr_info("tps68470-irled: TPS68470 が見つかりません\n");
		return -ENODEV;
	}

	regmap = dev_get_regmap(dev, NULL);
	if (!regmap) {
		dev_err(dev, "regmap を取得できません\n");
		put_device(dev);
		return -ENODEV;
	}

	/* REVID を読んで疎通を確認する (TPS68470 は 0x21) */
	ret = regmap_read(regmap, 0xff, &val);
	if (ret || val != 0x21) {
		dev_err(dev, "REVID が想定外です (ret=%d val=0x%02x)\n", ret, val);
		put_device(dev);
		return -ENODEV;
	}

	g_led = kzalloc(sizeof(*g_led), GFP_KERNEL);
	if (!g_led) {
		put_device(dev);
		return -ENOMEM;
	}

	g_led->regmap = regmap;
	g_led->dev = dev;
	g_led->cdev.name = "tps68470::ir_illuminator";
	g_led->cdev.max_brightness = TPS68470_IRLED_MAX;
	g_led->cdev.brightness_set_blocking = tps68470_irled_set;
	g_led->cdev.flags = LED_CORE_SUSPENDRESUME;

	ret = led_classdev_register(dev, &g_led->cdev);
	if (ret) {
		dev_err(dev, "LED クラスの登録に失敗しました: %d\n", ret);
		kfree(g_led);
		g_led = NULL;
		put_device(dev);
		return ret;
	}

	dev_info(dev, "IR 照明を /sys/class/leds/%s として登録しました (0-%d)\n",
		 g_led->cdev.name, TPS68470_IRLED_MAX);
	return 0;
}

static void __exit tps68470_irled_exit(void)
{
	if (!g_led)
		return;
	tps68470_irled_set(&g_led->cdev, 0);
	led_classdev_unregister(&g_led->cdev);
	put_device(g_led->dev);
	kfree(g_led);
	g_led = NULL;
}

module_init(tps68470_irled_init);
module_exit(tps68470_irled_exit);

MODULE_DESCRIPTION("TPS68470 IR illuminator as a LED class device");
MODULE_LICENSE("GPL");
