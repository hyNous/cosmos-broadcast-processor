"""
小宇宙播客下载器 - 入口
"""
import sys

from PyQt6.QtWidgets import QApplication

from gui import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("小宇宙播客下载器")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
