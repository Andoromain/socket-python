# -*- coding: utf-8 -*-

# Form implementation generated from reading ui file 'monIp.ui'
#
# Created: Tue Oct  8 19:14:15 2019
#      by: PyQt4 UI code generator 4.10
#
# WARNING! All changes made in this file will be lost!

from PyQt4 import QtCore, QtGui

try:
    _fromUtf8 = QtCore.QString.fromUtf8
except AttributeError:
    def _fromUtf8(s):
        return s

try:
    _encoding = QtGui.QApplication.UnicodeUTF8
    def _translate(context, text, disambig):
        return QtGui.QApplication.translate(context, text, disambig, _encoding)
except AttributeError:
    def _translate(context, text, disambig):
        return QtGui.QApplication.translate(context, text, disambig)

class Ui_monIP(object):
    def envoyerIP(self):
            f=open("monIP.ip",'w')
            f.write(str(self.IP.text()))
            f.close()
            self.ok.setEnabled(False)
            self.IP.setEnabled(False)
    def setupUi(self, monIP):
        monIP.setObjectName(_fromUtf8("monIP"))
        monIP.resize(294, 117)
        monIP.setMaximumSize(QtCore.QSize(294, 117))
        icon = QtGui.QIcon()
        icon.addPixmap(QtGui.QPixmap(_fromUtf8("images_002.png")), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        monIP.setWindowIcon(icon)
        self.label = QtGui.QLabel(monIP)
        self.label.setGeometry(QtCore.QRect(110, 30, 131, 20))
        font = QtGui.QFont()
        font.setFamily(_fromUtf8("Modern No. 20"))
        font.setPointSize(11)
        self.label.setFont(font)
        self.label.setObjectName(_fromUtf8("label"))
        self.IP = QtGui.QLineEdit(monIP)
        self.IP.setGeometry(QtCore.QRect(10, 70, 151, 20))
        self.IP.setObjectName(_fromUtf8("IP"))
        self.ok = QtGui.QPushButton(monIP)
        self.ok.setEnabled(True)
        self.ok.setGeometry(QtCore.QRect(180, 70, 41, 23))
        self.ok.setObjectName(_fromUtf8("ok"))
        self.exit = QtGui.QPushButton(monIP)
        self.exit.setGeometry(QtCore.QRect(230, 70, 51, 23))
        self.exit.setObjectName(_fromUtf8("exit"))

        self.retranslateUi(monIP)
        self.ok.clicked.connect(self.envoyerIP)
        self.exit.clicked.connect(monIP.close)
        QtCore.QMetaObject.connectSlotsByName(monIP)

    def retranslateUi(self, monIP):
        monIP.setWindowTitle(_translate("monIP", "Dialog", None))
        self.label.setText(_translate("monIP", "Saisir ton nom", None))
        self.ok.setText(_translate("monIP", "OK", None))
        self.exit.setText(_translate("monIP", "Exit", None))


if __name__ == "__main__":
    import sys
    app = QtGui.QApplication(sys.argv)
    monIP = QtGui.QDialog()
    ui = Ui_monIP()
    ui.setupUi(monIP)
    monIP.show()
    sys.exit(app.exec_())

