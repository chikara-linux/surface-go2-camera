// ロック画面が固まったときの復旧タイル。
// 実処理と回数制限は ~/.local/bin/lockscreen-restart.sh 側にある。
import org.kde.plasma.private.mobileshell.quicksettingsplugin as QS
import org.kde.plasma.private.mobileshell.state as MobileShellState
import org.kde.plasma.plasma5support as Plasma5Support

QS.QuickSetting {
    text: i18n("Restart Lock Screen")
    icon: "system-lock-screen"
    status: i18n("Once per lock")
    enabled: false

    function toggle() {
        executable.exec("/usr/local/bin/lockscreen-restart")
        MobileShellState.ShellDBusClient.closeActionDrawer();
    }

    Plasma5Support.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []
        onNewData: (sourceName, data) => {
            disconnectSource(sourceName)
        }
        function exec(cmd) {
            if (cmd) {
                connectSource(cmd)
            }
        }
    }
}
