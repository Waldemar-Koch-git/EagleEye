from PyQt6 import QtCore, QtWidgets

from .i18n import tr


class CameraManagerDialog(QtWidgets.QDialog):
    """Show all configured cameras in a non-modal table."""
    COLUMN_KEYS = [
        "manager.column.name",
        "manager.column.type",
        "manager.column.main_url",
        "manager.column.sub_stream",
        "manager.column.active",
        "",
    ]

    def __init__(self, main_window, parent=None):
        """Build the non-modal camera overview table and start its
        periodic auto-refresh timer."""
        super().__init__(parent)
        self.main_window = main_window
        self.setWindowTitle(tr("manager.title"))
        self.resize(900, 420)
        self.setModal(False)

        layout = QtWidgets.QVBoxLayout(self)

        toolbar_row = QtWidgets.QHBoxLayout()
        self.add_btn = QtWidgets.QPushButton(tr("manager.add"))
        self.add_btn.clicked.connect(self._add_camera)
        toolbar_row.addWidget(self.add_btn)
        toolbar_row.addStretch()
        self.refresh_btn = QtWidgets.QPushButton(tr("manager.refresh"))
        self.refresh_btn.clicked.connect(self.refresh)
        toolbar_row.addWidget(self.refresh_btn)
        layout.addLayout(toolbar_row)

        self.table = QtWidgets.QTableWidget(0, len(self.COLUMN_KEYS))
        self.table.setHorizontalHeaderLabels(self._column_labels())
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.cellDoubleClicked.connect(self._on_row_double_clicked)
        layout.addWidget(self.table)

        # Refresh periodically so status and FPS changes remain visible while
        # the dialog is open.
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(2000)

        self.refresh()

    def _column_labels(self):
        """Return localized table header labels."""
        return [tr(key) if key else "" for key in self.COLUMN_KEYS]

    def retranslate_ui(self):
        """Refresh translatable labels after a language switch."""
        self.setWindowTitle(tr("manager.title"))
        self.add_btn.setText(tr("manager.add"))
        self.refresh_btn.setText(tr("manager.refresh"))
        self.table.setHorizontalHeaderLabels(self._column_labels())

    def refresh(self):
        """Rebuild the table rows from the main window's current stream
        list. Called on a timer and after any add/edit/remove action."""
        self.setWindowTitle(tr("manager.title"))
        self.table.setHorizontalHeaderLabels(self._column_labels())
        mw = self.main_window
        self.table.setRowCount(len(mw.order))
        for row, sid in enumerate(mw.order):
            cfg = mw.streams.get(sid)
            if not cfg:
                continue

            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(cfg.name))
            type_label = cfg.stream_type.value
            if cfg.has_sub_stream():
                type_label += f" / {cfg.sub_stream_type.value}"
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(type_label))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(cfg.url))
            sub_label = cfg.sub_url if cfg.has_sub_stream() else "—"
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(sub_label))

            check = QtWidgets.QCheckBox()
            check.setChecked(cfg.enabled)
            check.stateChanged.connect(
                lambda state, s=sid: self.main_window.set_stream_enabled(
                    s, state == QtCore.Qt.CheckState.Checked.value))
            check_container = QtWidgets.QWidget()
            check_layout = QtWidgets.QHBoxLayout(check_container)
            check_layout.addWidget(check)
            check_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            check_layout.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(row, 4, check_container)

            actions = QtWidgets.QWidget()
            actions_layout = QtWidgets.QHBoxLayout(actions)
            actions_layout.setContentsMargins(2, 2, 2, 2)
            edit_btn = QtWidgets.QPushButton(tr("manager.edit"))
            edit_btn.clicked.connect(lambda _, s=sid: self._edit_camera(s))
            remove_btn = QtWidgets.QPushButton(tr("manager.remove"))
            remove_btn.clicked.connect(lambda _, s=sid: self._remove_camera(s))
            actions_layout.addWidget(edit_btn)
            actions_layout.addWidget(remove_btn)
            self.table.setCellWidget(row, 5, actions)

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QtWidgets.QHeaderView.ResizeMode.Stretch)

    def _on_row_double_clicked(self, row, _col):
        """Open the settings dialog for the double-clicked row's camera."""
        if 0 <= row < len(self.main_window.order):
            sid = self.main_window.order[row]
            self._edit_camera(sid)

    def _add_camera(self):
        """Open the "add camera" dialog and refresh the table afterwards."""
        self.main_window.add_stream_dialog()
        self.refresh()

    def _edit_camera(self, stream_id):
        """Open the settings dialog for stream_id and refresh the table."""
        self.main_window.open_settings_for(stream_id)
        self.refresh()

    def _remove_camera(self, stream_id):
        """Ask for confirmation, then remove stream_id and refresh the table."""
        cfg = self.main_window.streams.get(stream_id)
        name = cfg.name if cfg else stream_id
        reply = QtWidgets.QMessageBox.question(
            self, tr("dialog.remove_camera.title"),
            tr("dialog.remove_camera.message", name=name),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            self.main_window.remove_stream(stream_id)
            self.refresh()

    def closeEvent(self, event):
        """Stop the auto-refresh timer when the dialog is closed."""
        self._timer.stop()
        super().closeEvent(event)
