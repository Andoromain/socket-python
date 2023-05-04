# -*- coding: utf-8 -*-

# Form implementation generated from reading ui file 'sonIP.ui'
#
# Created: Tue Oct  8 19:14:19 2019
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

class Ui_sonIP(object):
    def envoyerIP(self):
            f=open("sonIP.ip",'w')
            f.write(str(self.IP.text()))
            f.close()
            self.ok.setEnabled(False)
            self.IP.setEnabled(False)
    def setupUi(self, sonIP):
        sonIP.setObjectName(_fromUtf8("sonIP"))
        sonIP.resize(294, 117)
        sonIP.setMaximumSize(QtCore.QSize(294, 117))
        icon = QtGui.QIcon()
        icon.addPixmap(QtGui.QPixmap(_fromUtf8("images_002.png")), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        sonIP.setWindowIcon(icon)
        self.buttonBox = QtGui.QDialogButtonBox(sonIP)
        self.buttonBox.setGeometry(QtCore.QRect(30, 240, 341, 32))
        self.buttonBox.setOrientation(QtCore.Qt.Horizontal)
        self.buttonBox.setStandardButtons(QtGui.QDialogButtonBox.Cancel|QtGui.QDialogButtonBox.Ok)
        self.buttonBox.setObjectName(_fromUtf8("buttonBox"))
        self.label = QtGui.QLabel(sonIP)
        self.label.setGeometry(QtCore.QRect(70, 30, 191, 20))
        font = QtGui.QFont()
        font.setFamily(_fromUtf8("Modern No. 20"))
        font.setPointSize(11)
        self.label.setFont(font)
        self.label.setObjectName(_fromUtf8("label"))
        self.IP = QtGui.QLineEdit(sonIP)
        self.IP.setGeometry(QtCore.QRect(10, 70, 151, 20))
        self.IP.setObjectName(_fromUtf8("IP"))
        self.ok = QtGui.QPushButton(sonIP)
        self.ok.setGeometry(QtCore.QRect(180, 70, 41, 23))
        self.ok.setObjectName(_fromUtf8("ok"))
        self.exit = QtGui.QPushButton(sonIP)
        self.exit.setGeometry(QtCore.QRect(240, 70, 41, 23))
        self.exit.setObjectName(_fromUtf8("exit"))

        self.retranslateUi(sonIP)
        self.ok.clicked.connect(self.envoyerIP)
        self.exit.clicked.connect(sonIP.close)
        QtCore.QObject.connect(self.buttonBox, QtCore.SIGNAL(_fromUtf8("accepted()")), sonIP.accept)
        QtCore.QObject.connect(self.buttonBox, QtCore.SIGNAL(_fromUtf8("rejected()")), sonIP.reject)
        QtCore.QMetaObject.connectSlotsByName(sonIP)

    def retranslateUi(self, sonIP):
        sonIP.setWindowTitle(_translate("sonIP", "Dialog", None))
        self.label.setText(_translate("sonIP", "Saisir l\' IP de votre Ami", None))
        self.ok.setText(_translate("sonIP", "OK", None))
        self.exit.setText(_translate("sonIP", "Exit", None))


if __name__ == "__main__":
    import sys
    app = QtGui.QApplication(sys.argv)
    sonIP = QtGui.QDialog()
    ui = Ui_sonIP()
    ui.setupUi(sonIP)
    sonIP.show()
    sys.exit(app.exec_())

