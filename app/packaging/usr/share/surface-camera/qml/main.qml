import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import QtMultimedia

Kirigami.ApplicationWindow {
    id: root
    title: "カメラ"
    visible: true
    width: 960
    height: 720

    // 撮影画面。ギャラリーは pageStack に push する。
    // 前面にいないときはカメラを解放する。撮影していないのに
    // ずっと掴んだままにしない。
    onActiveChanged: camera.setActive(active)

    pageStack.initialPage: Kirigami.Page {
        id: cameraPage
        padding: 0
        title: "カメラ"

        // 映像の外側は黒。カメラアプリで白地は眩しい。
        Rectangle {
            anchors.fill: parent
            color: "black"
        }

        VideoOutput {
            objectName: "videoOut"
            anchors.fill: parent
            fillMode: VideoOutput.PreserveAspectFit
        }

        // 上部: 解像度 / カメラ切替 / 明るさ
        RowLayout {
            anchors { top: parent.top; left: parent.left; right: parent.right }
            anchors.margins: Kirigami.Units.smallSpacing
            spacing: Kirigami.Units.smallSpacing

            QQC2.Button {
                text: camera.resolutionLabel
                icon.name: "zoom-fit-best"
                onClicked: camera.cycleResolution()
            }
            Item { Layout.fillWidth: true }
            QQC2.Button {
                visible: camera.hasMultipleCameras
                text: camera.cameraLabel
                icon.name: "camera-video"
                onClicked: camera.switchCamera()
            }
            Item { Layout.fillWidth: true }
            QQC2.Button {
                text: camera.brightnessLabel
                icon.name: "contrast"
                onClicked: camera.cycleBrightness()
            }
        }

        // 下部: サムネイル / シャッター / QR
        Item {
            anchors { bottom: parent.bottom; left: parent.left; right: parent.right }
            height: shutter.height + Kirigami.Units.largeSpacing * 2

            Rectangle {
                anchors.fill: parent
                color: Qt.rgba(0, 0, 0, 0.35)
            }

            // サムネイル（このセッションで撮った最後の1枚）
            QQC2.AbstractButton {
                id: thumb
                anchors { left: parent.left; leftMargin: Kirigami.Units.largeSpacing
                          verticalCenter: parent.verticalCenter }
                width: Kirigami.Units.gridUnit * 3
                height: width
                enabled: camera.shots.length > 0
                opacity: enabled ? 1.0 : 0.35
                onClicked: root.pageStack.push(galleryComponent)

                Rectangle {
                    anchors.fill: parent
                    radius: Kirigami.Units.smallSpacing
                    color: "transparent"
                    border.width: 2
                    border.color: Kirigami.Theme.textColor
                    clip: true
                    Image {
                        anchors.fill: parent
                        anchors.margins: 2
                        source: camera.lastShot
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                        sourceSize.width: 160
                    }
                }
            }

            // シャッター
            QQC2.AbstractButton {
                id: shutter
                anchors.centerIn: parent
                width: Kirigami.Units.gridUnit * 4
                height: width
                onClicked: camera.capture()
                Rectangle {
                    anchors.fill: parent
                    radius: width / 2
                    color: shutter.pressed ? Kirigami.Theme.hoverColor : "white"
                    border.width: 4
                    border.color: Qt.rgba(0, 0, 0, 0.35)
                }
            }

            // QR 読み取りモード
            QQC2.Button {
                anchors { right: parent.right; rightMargin: Kirigami.Units.largeSpacing
                          verticalCenter: parent.verticalCenter }
                text: camera.scanning ? "停止" : "QR"
                icon.name: camera.scanning ? "media-playback-stop" : "view-barcode-qr"
                highlighted: camera.scanning
                onClicked: camera.toggleScan()
            }
        }

        // 通知
        Kirigami.InlineMessage {
            id: toast
            anchors { top: parent.top; horizontalCenter: parent.horizontalCenter }
            anchors.topMargin: Kirigami.Units.gridUnit * 3
            width: Math.min(parent.width - Kirigami.Units.gridUnit * 2,
                            Kirigami.Units.gridUnit * 30)
            type: Kirigami.MessageType.Information
            showCloseButton: false
            Timer {
                id: toastTimer
                interval: 4000
                onTriggered: toast.visible = false
            }
        }

        Keys.onSpacePressed: camera.capture()
        Keys.onEscapePressed: if (camera.scanning) camera.toggleScan()
        focus: true
    }

    // ギャラリー（このセッションで撮った写真だけ）
    Component {
        id: galleryComponent
        Kirigami.Page {
            id: galleryPage
            padding: 0
            title: "撮影した写真 (%1)".arg(camera.shots.length)

            // 戻る導線。オーバーレイ主体の画面なのでツールバーの
            // 自動の戻るボタンが見えない。明示的に置く。
            actions: [
                Kirigami.Action {
                    text: "閉じる"
                    icon.name: "dialog-close"
                    onTriggered: root.pageStack.pop()
                },
                Kirigami.Action {
                    text: "フォルダを開く"
                    icon.name: "folder-open"
                    onTriggered: camera.openFolder(camera.lastShot)
                }
            ]

            Rectangle { anchors.fill: parent; color: "black" }

            ListView {
                id: filmstrip
                anchors.fill: parent
                model: camera.shots
                orientation: ListView.Horizontal
                snapMode: ListView.SnapOneItem
                highlightRangeMode: ListView.StrictlyEnforceRange
                clip: true
                currentIndex: count - 1
                delegate: Item {
                    width: filmstrip.width
                    height: filmstrip.height
                    Image {
                        anchors.fill: parent
                        anchors.margins: Kirigami.Units.smallSpacing
                        source: modelData
                        fillMode: Image.PreserveAspectFit
                        asynchronous: true
                    }
                }
            }

            // 何枚目か
            QQC2.Label {
                anchors { bottom: parent.bottom; horizontalCenter: parent.horizontalCenter }
                anchors.bottomMargin: Kirigami.Units.largeSpacing
                text: "%1 / %2".arg(filmstrip.currentIndex + 1).arg(filmstrip.count)
                color: "white"
                padding: Kirigami.Units.smallSpacing
                background: Rectangle { color: Qt.rgba(0,0,0,0.6); radius: 4 }
            }

            Keys.onEscapePressed: root.pageStack.pop()
        }
    }

    // QR の結果
    Kirigami.PromptDialog {
        id: qrDialog
        title: "QR コード"
        property string payload: ""
        standardButtons: Kirigami.Dialog.Close
        customFooterActions: [
            Kirigami.Action {
                text: "コピー"
                icon.name: "edit-copy"
                onTriggered: { qrText.selectAll(); qrText.copy(); qrText.deselect() }
            },
            Kirigami.Action {
                text: "開く"
                icon.name: "internet-web-browser"
                visible: qrDialog.payload.startsWith("http")
                onTriggered: Qt.openUrlExternally(qrDialog.payload)
            }
        ]
        QQC2.TextArea {
            id: qrText
            text: qrDialog.payload
            readOnly: true
            wrapMode: TextEdit.WrapAnywhere
        }
    }

    Connections {
        target: camera
        function onToast(text) {
            toast.text = text
            toast.visible = true
            toastTimer.restart()
        }
        function onQrFound(text) {
            qrDialog.payload = text
            qrDialog.open()
        }
    }
}
