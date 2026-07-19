import uuid

from PyQt6 import QtWidgets

from .i18n import tr
from .stream_config import StreamConfig
from .stream_types import StreamType


class StreamSettingsDialog(QtWidgets.QDialog):
    def __init__(self, config: StreamConfig = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("settings.title"))
        self.config = config or StreamConfig(
            id=str(uuid.uuid4()), name=tr("settings.default_name"), url="")

        form = QtWidgets.QFormLayout(self)

        self.name_edit = QtWidgets.QLineEdit(self.config.name)
        form.addRow(tr("settings.name"), self.name_edit)

        self.url_edit = QtWidgets.QLineEdit(self.config.url)
        self.url_edit.setPlaceholderText(tr("settings.url_placeholder"))
        form.addRow(tr("settings.main_url"), self.url_edit)

        self.type_combo = QtWidgets.QComboBox()
        types = list(StreamType)
        for t in types:
            self.type_combo.addItem(t.value, t)
        self.type_combo.setCurrentIndex(types.index(self.config.stream_type))
        form.addRow(tr("settings.main_type"), self.type_combo)

        form.addRow(QtWidgets.QLabel(tr("settings.sub_header")))

        self.sub_url_edit = QtWidgets.QLineEdit(self.config.sub_url)
        self.sub_url_edit.setPlaceholderText(tr("settings.sub_placeholder"))
        form.addRow(tr("settings.sub_url"), self.sub_url_edit)

        self.sub_type_combo = QtWidgets.QComboBox()
        for t in types:
            self.sub_type_combo.addItem(t.value, t)
        self.sub_type_combo.setCurrentIndex(types.index(self.config.sub_stream_type))
        form.addRow(tr("settings.sub_type"), self.sub_type_combo)

        self.active_variant_combo = QtWidgets.QComboBox()
        self.active_variant_combo.addItem(tr("stream.main"), "main")
        self.active_variant_combo.addItem(tr("stream.sub"), "sub")
        self.active_variant_combo.setCurrentIndex(
            0 if self.config.active_variant == "main" else 1)
        form.addRow(tr("settings.active_variant"), self.active_variant_combo)

        self.user_edit = QtWidgets.QLineEdit(self.config.username)
        form.addRow(tr("settings.user"), self.user_edit)

        self.pass_edit = QtWidgets.QLineEdit(self.config.password)
        self.pass_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        form.addRow(tr("settings.password"), self.pass_edit)

        self.snapshot_spin = QtWidgets.QSpinBox()
        self.snapshot_spin.setRange(20, 10000)
        self.snapshot_spin.setSuffix(" ms")
        self.snapshot_spin.setValue(self.config.snapshot_interval_ms)
        form.addRow(tr("settings.snapshot_interval"), self.snapshot_spin)

        self.scale_spin = QtWidgets.QDoubleSpinBox()
        self.scale_spin.setRange(0.1, 1.0)
        self.scale_spin.setSingleStep(0.1)
        self.scale_spin.setValue(self.config.scale)
        form.addRow(tr("settings.decode_scale"), self.scale_spin)

        self.fps_spin = QtWidgets.QSpinBox()
        self.fps_spin.setRange(1, 60)
        self.fps_spin.setValue(self.config.max_fps)
        form.addRow(tr("settings.max_fps"), self.fps_spin)

        self.rotate_combo = QtWidgets.QComboBox()
        self.rotate_combo.addItems(["0", "90", "180", "270"])
        self.rotate_combo.setCurrentText(str(self.config.rotate))
        form.addRow(tr("settings.rotation"), self.rotate_combo)

        self.reconnect_spin = QtWidgets.QDoubleSpinBox()
        self.reconnect_spin.setRange(0.2, 30.0)
        self.reconnect_spin.setValue(self.config.reconnect_delay_s)
        form.addRow(tr("settings.reconnect_delay"), self.reconnect_spin)

        self.enabled_check = QtWidgets.QCheckBox(tr("settings.enabled"))
        self.enabled_check.setChecked(self.config.enabled)
        form.addRow(self.enabled_check)

        self.mute_check = QtWidgets.QCheckBox(tr("settings.mute_errors"))
        self.mute_check.setChecked(self.config.mute_errors)
        form.addRow(self.mute_check)

        self.show_fps_check = QtWidgets.QCheckBox(tr("settings.show_fps"))
        self.show_fps_check.setChecked(self.config.show_fps)
        self.show_fps_check.setToolTip(tr("settings.show_fps_tooltip"))
        form.addRow(self.show_fps_check)

        self.hw_accel_check = QtWidgets.QCheckBox(tr("settings.hw_accel"))
        self.hw_accel_check.setChecked(self.config.hw_accel)
        self.hw_accel_check.setToolTip(tr("settings.hw_accel_tooltip"))
        form.addRow(self.hw_accel_check)

        self.audio_check = QtWidgets.QCheckBox(tr("settings.audio"))
        self.audio_check.setChecked(self.config.audio_enabled)
        self.audio_check.setToolTip(tr("settings.audio_tooltip"))
        form.addRow(self.audio_check)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok |
            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText(tr("button.ok"))
        btns.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText(tr("button.cancel"))
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def get_config(self) -> StreamConfig:
        self.config.name = self.name_edit.text().strip() or tr("settings.fallback_name")
        self.config.url = self.url_edit.text().strip()
        self.config.stream_type = self.type_combo.currentData()
        self.config.sub_url = self.sub_url_edit.text().strip()
        self.config.sub_stream_type = self.sub_type_combo.currentData()
        self.config.active_variant = self.active_variant_combo.currentData()
        self.config.enabled = self.enabled_check.isChecked()
        self.config.username = self.user_edit.text()
        self.config.password = self.pass_edit.text()
        self.config.snapshot_interval_ms = self.snapshot_spin.value()
        self.config.scale = self.scale_spin.value()
        self.config.max_fps = self.fps_spin.value()
        self.config.rotate = int(self.rotate_combo.currentText())
        self.config.reconnect_delay_s = self.reconnect_spin.value()
        self.config.mute_errors = self.mute_check.isChecked()
        self.config.show_fps = self.show_fps_check.isChecked()
        self.config.hw_accel = self.hw_accel_check.isChecked()
        self.config.audio_enabled = self.audio_check.isChecked()
        return self.config
