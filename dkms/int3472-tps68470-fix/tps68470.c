// SPDX-License-Identifier: GPL-2.0
/* Author: Dan Scally <djrscally@gmail.com> */

#include <linux/acpi.h>
#include <linux/delay.h>
#include <linux/i2c.h>
#include <linux/kernel.h>
#include <linux/mfd/core.h>
#include <linux/mfd/tps68470.h>
#include <linux/platform_device.h>
#include <linux/pm.h>
#include <linux/platform_data/tps68470.h>
#include <linux/platform_data/x86/int3472.h>
#include <linux/regmap.h>
#include <linux/string.h>

#include "tps68470.h"

#define DESIGNED_FOR_CHROMEOS		1
#define DESIGNED_FOR_WINDOWS		2

#define TPS68470_WIN_MFD_CELL_COUNT	3

static const struct mfd_cell tps68470_cros[] = {
	{ .name = "tps68470-gpio" },
	{ .name = "tps68470_pmic_opregion" },
};

static const struct regmap_config tps68470_regmap_config = {
	.reg_bits = 8,
	.val_bits = 8,
	.max_register = TPS68470_REG_MAX,
};

/*
 * Hibernation support.
 *
 * The TPS68470 loses power across hibernation and comes back with its reset
 * defaults: every LDO at its lowest voltage (0.875 V) and the PLL dividers
 * cleared. Nothing re-programs it afterwards. clk-tps68470 writes the PLL
 * configuration in its set_rate() callback, which the clk core does not call
 * again for an unchanged rate, and the regulator core applies the board
 * voltages once, when the regulators are registered. The restored kernel
 * believes the chip is still configured, so the sensors are neither powered
 * nor clocked and fail with -EREMOTEIO until the next reboot.
 *
 * Save the configuration registers when the hibernation image is created
 * and write them back when it is restored. The order honours the chip's own
 * dependencies: the PLL is configured before it is enabled, the PLL runs
 * before the CORE buck is enabled, voltages are set before the rails are
 * enabled, and GPIO output levels are set before their direction.
 *
 * Consumers (the sensors and the VCM) have device links to the regulators,
 * whose parent is this device, so this runs before their own restore.
 */
static const u8 tps68470_pm_regs[] = {
	/* PLL configuration, as written by clk-tps68470's set_rate() */
	TPS68470_REG_POSTDIV2,
	TPS68470_REG_BOOSTDIV,
	TPS68470_REG_BUCKDIV,
	TPS68470_REG_PLLSWR,
	TPS68470_REG_XTALDIV,
	TPS68470_REG_PLLDIV,
	TPS68470_REG_POSTDIV,
	TPS68470_REG_PLLCTL2,
	TPS68470_REG_CLKCFG2,
	/* Clock outputs, then the PLL enable, as in clk-tps68470's prepare() */
	TPS68470_REG_CLKCFG1,
	TPS68470_REG_PLLCTL,
	/* Rail voltages */
	TPS68470_REG_VCMVAL,
	TPS68470_REG_VAUX1VAL,
	TPS68470_REG_VAUX2VAL,
	TPS68470_REG_VIOVAL,
	TPS68470_REG_VSIOVAL,
	TPS68470_REG_VAVAL,
	TPS68470_REG_VDVAL,
	/* Rail enables */
	TPS68470_REG_S_I2C_CTL,
	TPS68470_REG_VCMCTL,
	TPS68470_REG_VAUX1CTL,
	TPS68470_REG_VAUX2CTL,
	TPS68470_REG_VACTL,
	TPS68470_REG_VDCTL,
	/* GPIO output levels, then direction and mode */
	TPS68470_REG_SGPO,
	TPS68470_REG_GPDO,
	TPS68470_REG_GPCTL0A,
	TPS68470_REG_GPCTL0B,
	TPS68470_REG_GPCTL1A,
	TPS68470_REG_GPCTL1B,
	TPS68470_REG_GPCTL2A,
	TPS68470_REG_GPCTL2B,
	TPS68470_REG_GPCTL3A,
	TPS68470_REG_GPCTL3B,
	TPS68470_REG_GPCTL4A,
	TPS68470_REG_GPCTL4B,
	TPS68470_REG_GPCTL5A,
	TPS68470_REG_GPCTL5B,
	TPS68470_REG_GPCTL6A,
	TPS68470_REG_GPCTL6B,
};

struct tps68470_pm_state {
	unsigned int val[ARRAY_SIZE(tps68470_pm_regs)];
	bool valid;
};

/*
 * The client data is the regmap, which the MFD cells look up through their
 * parent, so the saved state is kept as a device resource instead.
 */
static void tps68470_pm_state_release(struct device *dev, void *res)
{
}

static struct tps68470_pm_state *tps68470_pm_state(struct device *dev)
{
	return devres_find(dev, tps68470_pm_state_release, NULL, NULL);
}

static int tps68470_pm_freeze(struct device *dev)
{
	struct tps68470_pm_state *state = tps68470_pm_state(dev);
	struct regmap *regmap = dev_get_drvdata(dev);
	unsigned int i;
	int ret;

	if (!state || !regmap)
		return 0;

	state->valid = false;

	for (i = 0; i < ARRAY_SIZE(tps68470_pm_regs); i++) {
		ret = regmap_read(regmap, tps68470_pm_regs[i], &state->val[i]);
		if (ret) {
			/* Do not block hibernation; just skip the restore. */
			dev_warn(dev, "Failed to save register 0x%02x: %d\n",
				 tps68470_pm_regs[i], ret);
			return 0;
		}
	}

	state->valid = true;

	return 0;
}

static int tps68470_pm_restore(struct device *dev)
{
	struct tps68470_pm_state *state = tps68470_pm_state(dev);
	struct regmap *regmap = dev_get_drvdata(dev);
	unsigned int i;
	int ret;

	if (!state || !regmap || !state->valid)
		return 0;

	for (i = 0; i < ARRAY_SIZE(tps68470_pm_regs); i++) {
		ret = regmap_write(regmap, tps68470_pm_regs[i], state->val[i]);
		if (ret) {
			dev_err(dev, "Failed to restore register 0x%02x: %d\n",
				tps68470_pm_regs[i], ret);
			return ret;
		}

		/* Let the PLL lock, as clk-tps68470's prepare() does */
		if (tps68470_pm_regs[i] == TPS68470_REG_PLLCTL &&
		    (state->val[i] & TPS68470_PLL_EN_MASK))
			usleep_range(4000, 5000);
	}

	dev_info(dev, "Restored PMIC configuration after hibernation\n");

	return 0;
}

static const struct dev_pm_ops tps68470_pm_ops = {
	.freeze = tps68470_pm_freeze,
	.restore = tps68470_pm_restore,
};

static int tps68470_chip_init(struct device *dev, struct regmap *regmap)
{
	unsigned int version;
	int ret;

	/* Force software reset */
	ret = regmap_write(regmap, TPS68470_REG_RESET, TPS68470_REG_RESET_MASK);
	if (ret)
		return ret;

	ret = regmap_read(regmap, TPS68470_REG_REVID, &version);
	if (ret) {
		dev_err(dev, "Failed to read revision register: %d\n", ret);
		return ret;
	}

	dev_info(dev, "TPS68470 REVID: 0x%02x\n", version);

	return 0;
}

/** skl_int3472_tps68470_calc_type: Check what platform a device is designed for
 * @adev: A pointer to a &struct acpi_device
 *
 * Check CLDB buffer against the PMIC's adev. If present, then we check
 * the value of control_logic_type field and follow one of the
 * following scenarios:
 *
 *	1. No CLDB - likely ACPI tables designed for ChromeOS. We
 *	create platform devices for the GPIOs and OpRegion drivers.
 *
 *	2. CLDB, with control_logic_type = 2 - probably ACPI tables
 *	made for Windows 2-in-1 platforms. Register pdevs for GPIO,
 *	Clock and Regulator drivers to bind to.
 *
 *	3. Any other value in control_logic_type, we should never have
 *	gotten to this point; fail probe and return.
 *
 * Return:
 * * 1		Device intended for ChromeOS
 * * 2		Device intended for Windows
 * * -EINVAL	Where @adev has an object named CLDB but it does not conform to
 *		our expectations
 */
static int skl_int3472_tps68470_calc_type(struct acpi_device *adev)
{
	struct int3472_cldb cldb = { 0 };
	int ret;

	/*
	 * A CLDB buffer that exists, but which does not match our expectations
	 * should trigger an error so we don't blindly continue.
	 */
	ret = skl_int3472_fill_cldb(adev, &cldb);
	if (ret && ret != -ENODEV)
		return ret;

	if (ret)
		return DESIGNED_FOR_CHROMEOS;

	if (cldb.control_logic_type != 2)
		return -EINVAL;

	return DESIGNED_FOR_WINDOWS;
}

/*
 * Return the size of the flexible array member, because we'll need that later
 * on to pass .pdata_size to cells.
 */
static int
skl_int3472_fill_clk_pdata(struct device *dev, struct tps68470_clk_platform_data **clk_pdata)
{
	struct acpi_device *adev = ACPI_COMPANION(dev);
	struct acpi_device *consumer;
	unsigned int n_consumers = 0;
	const char *sensor_name;
	unsigned int i = 0;

	for_each_acpi_consumer_dev(adev, consumer)
		n_consumers++;

	if (!n_consumers) {
		dev_err(dev, "INT3472 seems to have no dependents\n");
		return -ENODEV;
	}

	*clk_pdata = devm_kzalloc(dev, struct_size(*clk_pdata, consumers, n_consumers),
				  GFP_KERNEL);
	if (!*clk_pdata)
		return -ENOMEM;

	(*clk_pdata)->n_consumers = n_consumers;
	i = 0;

	for_each_acpi_consumer_dev(adev, consumer) {
		sensor_name = devm_kasprintf(dev, GFP_KERNEL, I2C_DEV_NAME_FORMAT,
					     acpi_dev_name(consumer));
		if (!sensor_name) {
			acpi_dev_put(consumer);
			return -ENOMEM;
		}

		(*clk_pdata)->consumers[i].consumer_dev_name = sensor_name;
		i++;
	}

	return n_consumers;
}

static int skl_int3472_tps68470_probe(struct i2c_client *client)
{
	struct acpi_device *adev = ACPI_COMPANION(&client->dev);
	const struct int3472_tps68470_board_data *board_data;
	struct tps68470_clk_platform_data *clk_pdata;
	struct tps68470_pm_state *pm_state;
	struct mfd_cell *cells;
	struct regmap *regmap;
	int n_consumers;
	int device_type;
	int ret;
	int i;

	if (!adev)
		return -ENODEV;

	n_consumers = skl_int3472_fill_clk_pdata(&client->dev, &clk_pdata);
	if (n_consumers < 0)
		return n_consumers;

	regmap = devm_regmap_init_i2c(client, &tps68470_regmap_config);
	if (IS_ERR(regmap)) {
		dev_err(&client->dev, "Failed to create regmap: %ld\n", PTR_ERR(regmap));
		return PTR_ERR(regmap);
	}

	i2c_set_clientdata(client, regmap);

	ret = tps68470_chip_init(&client->dev, regmap);
	if (ret < 0) {
		dev_err(&client->dev, "TPS68470 init error %d\n", ret);
		return ret;
	}

	pm_state = devres_alloc(tps68470_pm_state_release, sizeof(*pm_state),
				GFP_KERNEL);
	if (!pm_state)
		return -ENOMEM;
	devres_add(&client->dev, pm_state);

	device_type = skl_int3472_tps68470_calc_type(adev);
	switch (device_type) {
	case DESIGNED_FOR_WINDOWS:
		board_data = int3472_tps68470_get_board_data(dev_name(&client->dev));
		if (!board_data)
			return dev_err_probe(&client->dev, -ENODEV, "No board-data found for this model\n");

		cells = kzalloc_objs(*cells, TPS68470_WIN_MFD_CELL_COUNT);
		if (!cells)
			return -ENOMEM;

		/*
		 * The order of the cells matters here! The clk must be first
		 * because the regulator depends on it. The gpios must be last,
		 * acpi_gpiochip_add() calls acpi_dev_clear_dependencies() and
		 * the clk + regulators must be ready when this happens.
		 */
		cells[0].name = "tps68470-clk";
		cells[0].platform_data = clk_pdata;
		cells[0].pdata_size = struct_size(clk_pdata, consumers, n_consumers);
		cells[1].name = "tps68470-regulator";
		cells[1].platform_data = (void *)board_data->tps68470_regulator_pdata;
		cells[1].pdata_size = sizeof(struct tps68470_regulator_platform_data);
		cells[2].name = "tps68470-gpio";

		for (i = 0; i < board_data->n_gpiod_lookups; i++)
			gpiod_add_lookup_table(board_data->tps68470_gpio_lookup_tables[i]);

		ret = devm_mfd_add_devices(&client->dev, PLATFORM_DEVID_NONE,
					   cells, TPS68470_WIN_MFD_CELL_COUNT,
					   NULL, 0, NULL);
		kfree(cells);

		if (ret) {
			for (i = 0; i < board_data->n_gpiod_lookups; i++)
				gpiod_remove_lookup_table(board_data->tps68470_gpio_lookup_tables[i]);
		}

		break;
	case DESIGNED_FOR_CHROMEOS:
		ret = devm_mfd_add_devices(&client->dev, PLATFORM_DEVID_NONE,
					   tps68470_cros, ARRAY_SIZE(tps68470_cros),
					   NULL, 0, NULL);
		break;
	default:
		dev_err(&client->dev, "Failed to add MFD devices\n");
		return device_type;
	}

	/*
	 * No acpi_dev_clear_dependencies() here, since the acpi_gpiochip_add()
	 * for the GPIO cell already does this.
	 */

	return ret;
}

static void skl_int3472_tps68470_remove(struct i2c_client *client)
{
	const struct int3472_tps68470_board_data *board_data;
	int i;

	board_data = int3472_tps68470_get_board_data(dev_name(&client->dev));
	if (board_data) {
		for (i = 0; i < board_data->n_gpiod_lookups; i++)
			gpiod_remove_lookup_table(board_data->tps68470_gpio_lookup_tables[i]);
	}
}

static const struct acpi_device_id int3472_device_id[] = {
	{ "INT3472", 0 },
	{ }
};
MODULE_DEVICE_TABLE(acpi, int3472_device_id);

static struct i2c_driver int3472_tps68470 = {
	.driver = {
		.name = "int3472-tps68470",
		.acpi_match_table = int3472_device_id,
		.pm = pm_sleep_ptr(&tps68470_pm_ops),
	},
	.probe = skl_int3472_tps68470_probe,
	.remove = skl_int3472_tps68470_remove,
};
module_i2c_driver(int3472_tps68470);

MODULE_DESCRIPTION("Intel SkyLake INT3472 ACPI TPS68470 Device Driver");
MODULE_AUTHOR("Daniel Scally <djrscally@gmail.com>");
MODULE_LICENSE("GPL v2");
MODULE_IMPORT_NS("INTEL_INT3472");
MODULE_SOFTDEP("pre: clk-tps68470 tps68470-regulator");
