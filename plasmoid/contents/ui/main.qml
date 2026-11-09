pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as P5Support

PlasmoidItem {
    id: root
    preferredRepresentation: compactRepresentation
    activationTogglesExpanded: false
    property var payload: ({counts: {}, status: {}, ok: false})
    readonly property var worlds: ["dnf", "arch", "ubuntu", "flatpak"]
    readonly property var labels: ["DNF", "Arch / AUR", "Ubuntu", "Flatpak"]
    readonly property string helper: '"$HOME/.local/bin/pkg-updates"'
    readonly property string readCommand: helper + " state"
    readonly property bool hasErrors: worlds.some(w => payload.status[w] === "error") || !!payload.error
    readonly property int itemWidth: Kirigami.Units.iconSizes.smallMedium + metrics.advanceWidth + 16

    function countText(world) {
        if (payload.status[world] === "unavailable") return "–";
        const n = payload.counts[world];
        return n === null || n === undefined ? "?" : String(n);
    }
    function tooltip() {
        let text = worlds.map((w, i) => labels[i] + ": " + countText(w)).join(" · ");
        if (payload.checked_at) text += "\nChecked: " + new Date(payload.checked_at * 1000).toLocaleString();
        else text += "\nNot checked yet";
        if (hasErrors) text += "\nCheck failed — right-click to retry. Run pkg update for details.";
        return text + "\nClick to upgrade all · Right-click to check now or change the interval";
    }
    function upgrade() { executor.connectSource(helper + " launch"); }

    toolTipMainText: "pkg Updates"
    toolTipSubText: tooltip()
    Plasmoid.status: PlasmaCore.Types.ActiveStatus
    Plasmoid.contextualActions: [
        PlasmaCore.Action {
            text: "Check for updates now"
            icon.name: "view-refresh"
            onTriggered: executor.connectSource("systemctl --user start --no-block pkg-updates.service")
        },
        PlasmaCore.Action {
            text: "Check every day"
            icon.name: "view-calendar-day"
            onTriggered: executor.connectSource(root.helper + " schedule --hours 24")
        },
        PlasmaCore.Action {
            text: "Check every two days"
            icon.name: "view-calendar-week"
            onTriggered: executor.connectSource(root.helper + " schedule --hours 48")
        }
    ]

    TextMetrics {
        id: metrics
        font: Kirigami.Theme.smallFont
        text: root.worlds.map(w => root.countText(w)).join("  ")
    }
    P5Support.DataSource {
        id: executor
        engine: "executable"
        onNewData: (sourceName, data) => {
            if (sourceName === root.readCommand && data["exit code"] === 0) {
                try {
                    const doc = JSON.parse(data.stdout);
                    if (doc.counts && doc.status) root.payload = doc;
                } catch (e) { console.warn("pkg Updates: invalid state", e); }
            }
            disconnectSource(sourceName);
        }
    }
    Timer {
        interval: 15000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: executor.connectSource(root.readCommand)
    }
    compactRepresentation: Item {
        Layout.fillHeight: true
        Layout.minimumWidth: root.itemWidth
        Layout.preferredWidth: root.itemWidth
        Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
        implicitWidth: root.itemWidth
        implicitHeight: Kirigami.Units.iconSizes.smallMedium
        Row {
            anchors.centerIn: parent
            spacing: 6
            Kirigami.Icon {
                anchors.verticalCenter: parent.verticalCenter
                width: Kirigami.Units.iconSizes.smallMedium
                height: width
                source: root.hasErrors ? "dialog-warning" : "system-software-update"
                opacity: root.payload.total > 0 || root.hasErrors ? 1 : 0.5
            }
            PlasmaComponents.Label {
                anchors.verticalCenter: parent.verticalCenter
                text: root.worlds.map(w => root.countText(w)).join("  ")
                font: Kirigami.Theme.smallFont
                opacity: root.payload.total > 0 || root.hasErrors ? 1 : 0.5
            }
        }
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.MiddleButton
            cursorShape: Qt.PointingHandCursor
            onClicked: mouse => {
                if (mouse.button === Qt.MiddleButton)
                    executor.connectSource("systemctl --user start --no-block pkg-updates.service");
                else root.upgrade();
            }
        }
        Accessible.role: Accessible.Button
        Accessible.name: "pkg Updates. " + root.tooltip()
        Accessible.onPressAction: root.upgrade()
    }
    // Plasma requires a full representation even for click-to-launch widgets.
    fullRepresentation: Item {
        implicitWidth: 260
        implicitHeight: 50
        PlasmaComponents.Label { anchors.centerIn: parent; text: "Click the panel icon to upgrade." }
    }
}
