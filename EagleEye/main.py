# Version 0.9
import logging
import sys

from PyQt6 import QtWidgets

from EagleEye.main_window import MainWindow


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("EagleEye")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
