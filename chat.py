# -*- coding: utf-8 -*-

# Form implementation generated from reading ui file 'chat.ui'
#
# Created: Tue Oct  8 20:14:41 2019
#      by: PyQt4 UI code generator 4.10
#
# WARNING! All changes made in this file will be lost!

from PyQt4 import QtCore, QtGui
import monIp
import sonIp
import socket
import select,sys
from threading import Thread
import time

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
class Affiche(Thread):
    def __init__(self,connecte):
        Thread.__init__(self)
        self.connect=connecte
    def run(self):
        global ui
        global connection
        global MainWindow
        while 1:
            msg_recu=self.connect.recv(1024).decode("Utf-8")
            affiche="lui  :  "+msg_recu
            ui.aff(affiche)
            if msg_recu=="FIN":
                self.connect.send(b"FIN")
                self.connect.close()
                connection.close()
                ui.indicateur.setText(_translate("MainWindow","deconnecte", None))
                time.sleep(10)
                MainWindow.close()
                break
class Ui_MainWindow(object):
    def aff(self,affiche):
        self.Message.append(_translate("MainWindow",affiche, None))
    def DemarerServeur(self):
        global server_lance
        if server_lance==False:
            client_connecte=[]
            connection.bind(('',12800))
            connection.listen(5)
            self.indicateur.setText(_translate("MainWindow","Le serveur ecoute du client!", None))
            ok=True
            i=0.05
            while ok:
                connexions_demandees, wlist, xlist =select.select([connection],[], [], i)
                if connexions_demandees != []:
                    connection_avec_client,infos_connection=connexions_demandees[0].accept()
                    ok=False
                    i+=0.05    
            self.indicateur.setText(_translate("MainWindow","connecte : client", None))
            server_lance=True
            self.r(connection_avec_client)
            self.envoyer.clicked.connect(self.sendMessageServer)
            th=Affiche(connection_avec_client)
            th.start()
    def r(self,conection):
        global conect
        conect=conection
    def RecevoirC(self):
        f=open("sonIP.ip")
        hote=f.read()
        if hote!=" ":
            try:
                connection.connect((hote,12800))
            except:
                self.indicateur.setText(_translate("MainWindow", "On ne peut pas connecter ", None))
            else:
                self.indicateur.setText(_translate("MainWindow", "connecte : server", None))
                global server_lance
                server_lance=True
                self.envoyer.clicked.connect(self.sendMessageClient)
                th=Affiche(connection)
                th.start()
        else:
             self.indicateur.setText(_translate("MainWindow", "Veuillez saisir l'IP de votre ami", None))
    def sendMessageClient(self):
        msg_send=self.message_a_envoyer.toPlainText()
        msg_send=msg_send.encode()
        connection.send(msg_send)
        self.message_a_envoyer.setText(_translate("MainWindow", "", None))
        affiche="moi  :  "+msg_send.decode()
        self.Message.append(_translate("MainWindow",affiche, None))
    def sendMessageServer(self):
        msg_send=self.message_a_envoyer.toPlainText()
        msg_send=msg_send.encode()
        conect.send(msg_send)
        self.message_a_envoyer.setText(_translate("MainWindow", "", None))
        affiche="moi  :  "+msg_send.decode()
        self.Message.append(_translate("MainWindow",affiche, None))
    def ouvrirmIP(self):
        self.monIP = QtGui.QDialog()
        fenetre=monIp.Ui_monIP()
        fenetre.setupUi(self.monIP)
        self.monIP.setWindowModality(QtCore.Qt.ApplicationModal)
        self.monIP.exec_()
    def ouvrirsIP(self):
         self.sonIP = QtGui.QDialog()
         fenetre=sonIp.Ui_sonIP()
         fenetre.setupUi(self.sonIP)
         self.sonIP.exec_()
         f=open("sonIP.ip")
         text=f.read()
         if text!="":
            self.actionRecevoir.setEnabled(True)
            self.actionDemarer.setEnabled(False)
    def setupUi(self, MainWindow):
        MainWindow.setObjectName(_fromUtf8("MainWindow"))
        MainWindow.resize(362, 448)
        MainWindow.setMinimumSize(QtCore.QSize(362, 448))
        MainWindow.setMaximumSize(QtCore.QSize(362, 448))
        icon = QtGui.QIcon()
        icon.addPixmap(QtGui.QPixmap(_fromUtf8("images_002.png")), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        MainWindow.setWindowIcon(icon)
        MainWindow.setStyleSheet(_fromUtf8(""))
        self.centralwidget = QtGui.QWidget(MainWindow)
        self.centralwidget.setObjectName(_fromUtf8("centralwidget"))
        self.gridLayout_2 = QtGui.QGridLayout(self.centralwidget)
        self.gridLayout_2.setObjectName(_fromUtf8("gridLayout_2"))
        spacerItem = QtGui.QSpacerItem(20, 20, QtGui.QSizePolicy.Minimum, QtGui.QSizePolicy.Fixed)
        self.gridLayout_2.addItem(spacerItem, 0, 3, 1, 1)
        spacerItem1 = QtGui.QSpacerItem(20, 18, QtGui.QSizePolicy.Minimum, QtGui.QSizePolicy.Fixed)
        self.gridLayout_2.addItem(spacerItem1, 0, 4, 1, 1)
        spacerItem2 = QtGui.QSpacerItem(40, 20, QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Minimum)
        self.gridLayout_2.addItem(spacerItem2, 1, 0, 1, 2)
        spacerItem3 = QtGui.QSpacerItem(40, 20, QtGui.QSizePolicy.Fixed, QtGui.QSizePolicy.Minimum)
        self.gridLayout_2.addItem(spacerItem3, 1, 2, 1, 1)
        self.indicateur = QtGui.QLabel(self.centralwidget)
        self.indicateur.setMinimumSize(QtCore.QSize(160, 20))
        self.indicateur.setStyleSheet(_fromUtf8(""))
        self.indicateur.setObjectName(_fromUtf8("indicateur"))
        self.gridLayout_2.addWidget(self.indicateur, 1, 3, 1, 1)
        spacerItem4 = QtGui.QSpacerItem(40, 20, QtGui.QSizePolicy.Fixed, QtGui.QSizePolicy.Minimum)
        self.gridLayout_2.addItem(spacerItem4, 1, 5, 1, 1)
        spacerItem5 = QtGui.QSpacerItem(36, 20, QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Minimum)
        self.gridLayout_2.addItem(spacerItem5, 2, 0, 1, 2)
        self.Message = QtGui.QTextEdit(self.centralwidget)
        self.Message.setMinimumSize(QtCore.QSize(270, 168))
        self.Message.setMaximumSize(QtCore.QSize(270, 168))
        self.Message.setStyleSheet(_fromUtf8("background-color: rgb(249, 242, 255);"))
        self.Message.setObjectName(_fromUtf8("Message"))
        self.gridLayout_2.addWidget(self.Message, 2, 2, 1, 4)
        spacerItem6 = QtGui.QSpacerItem(34, 20, QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Minimum)
        self.gridLayout_2.addItem(spacerItem6, 2, 6, 1, 1)
        spacerItem7 = QtGui.QSpacerItem(20, 15, QtGui.QSizePolicy.Minimum, QtGui.QSizePolicy.Fixed)
        self.gridLayout_2.addItem(spacerItem7, 3, 3, 1, 1)
        spacerItem8 = QtGui.QSpacerItem(36, 20, QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Minimum)
        self.gridLayout_2.addItem(spacerItem8, 4, 0, 1, 1)
        self.frame = QtGui.QFrame(self.centralwidget)
        self.frame.setFrameShape(QtGui.QFrame.StyledPanel)
        self.frame.setFrameShadow(QtGui.QFrame.Raised)
        self.frame.setObjectName(_fromUtf8("frame"))
        self.gridLayout = QtGui.QGridLayout(self.frame)
        self.gridLayout.setObjectName(_fromUtf8("gridLayout"))
        spacerItem9 = QtGui.QSpacerItem(6, 20, QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Minimum)
        self.gridLayout.addItem(spacerItem9, 0, 4, 1, 1)
        spacerItem10 = QtGui.QSpacerItem(6, 20, QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Minimum)
        self.gridLayout.addItem(spacerItem10, 0, 0, 1, 1)
        self.message_a_envoyer = QtGui.QTextEdit(self.frame)
        self.message_a_envoyer.setMinimumSize(QtCore.QSize(161, 31))
        self.message_a_envoyer.setMaximumSize(QtCore.QSize(161, 31))
        self.message_a_envoyer.setObjectName(_fromUtf8("message_a_envoyer"))
        self.gridLayout.addWidget(self.message_a_envoyer, 0, 1, 1, 1)
        self.envoyer = QtGui.QPushButton(self.frame)
        self.envoyer.setObjectName(_fromUtf8("envoyer"))
        self.gridLayout.addWidget(self.envoyer, 0, 2, 1, 2)
        spacerItem11 = QtGui.QSpacerItem(20, 6, QtGui.QSizePolicy.Minimum, QtGui.QSizePolicy.Expanding)
        self.gridLayout.addItem(spacerItem11, 1, 1, 1, 1)
        self.gridLayout_2.addWidget(self.frame, 4, 1, 2, 5)
        spacerItem12 = QtGui.QSpacerItem(34, 20, QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Minimum)
        self.gridLayout_2.addItem(spacerItem12, 4, 6, 1, 1)
        spacerItem13 = QtGui.QSpacerItem(20, 82, QtGui.QSizePolicy.Minimum, QtGui.QSizePolicy.Expanding)
        self.gridLayout_2.addItem(spacerItem13, 5, 0, 2, 1)
        spacerItem14 = QtGui.QSpacerItem(20, 82, QtGui.QSizePolicy.Minimum, QtGui.QSizePolicy.Expanding)
        self.gridLayout_2.addItem(spacerItem14, 5, 6, 2, 1)
        self.frame_2 = QtGui.QFrame(self.centralwidget)
        self.frame_2.setMinimumSize(QtCore.QSize(278, 110))
        self.frame_2.setFrameShape(QtGui.QFrame.StyledPanel)
        self.frame_2.setFrameShadow(QtGui.QFrame.Raised)
        self.frame_2.setObjectName(_fromUtf8("frame_2"))
        self.pushButton = QtGui.QPushButton(self.frame_2)
        self.pushButton.setGeometry(QtCore.QRect(80, 0, 111, 101))
        self.pushButton.setStyleSheet(_fromUtf8("border-image:url(\'sms (2).png\');"))
        self.pushButton.setText(_fromUtf8(""))
        self.pushButton.setObjectName(_fromUtf8("pushButton"))
        self.gridLayout_2.addWidget(self.frame_2, 6, 2, 1, 4)
        MainWindow.setCentralWidget(self.centralwidget)
        self.menubar = QtGui.QMenuBar(MainWindow)
        self.menubar.setGeometry(QtCore.QRect(0, 0, 362, 21))
        self.menubar.setObjectName(_fromUtf8("menubar"))
        self.menuMenu = QtGui.QMenu(self.menubar)
        self.menuMenu.setObjectName(_fromUtf8("menuMenu"))
        self.menuAide = QtGui.QMenu(self.menubar)
        self.menuAide.setObjectName(_fromUtf8("menuAide"))
        MainWindow.setMenuBar(self.menubar)
        self.statusbar = QtGui.QStatusBar(MainWindow)
        self.statusbar.setObjectName(_fromUtf8("statusbar"))
        MainWindow.setStatusBar(self.statusbar)
        self.actionEntrer_mon_IP = QtGui.QAction(MainWindow)
        self.actionEntrer_mon_IP.setObjectName(_fromUtf8("actionEntrer_mon_IP"))
        self.actionConnecter_a_un_ami = QtGui.QAction(MainWindow)
        self.actionConnecter_a_un_ami.setObjectName(_fromUtf8("actionConnecter_a_un_ami"))
        self.actionDemarer = QtGui.QAction(MainWindow)
        self.actionDemarer.setObjectName(_fromUtf8("actionDemarer"))
        self.actionRecevoir = QtGui.QAction(MainWindow)
        self.actionRecevoir.setObjectName(_fromUtf8("actionRecevoir"))
        self.actionQuiter = QtGui.QAction(MainWindow)
        self.actionQuiter.setObjectName(_fromUtf8("actionQuiter"))
        self.menuMenu.addAction(self.actionEntrer_mon_IP)
        self.menuMenu.addAction(self.actionConnecter_a_un_ami)
        self.menuMenu.addAction(self.actionDemarer)
        self.menuMenu.addAction(self.actionRecevoir)
        self.menuMenu.addAction(self.actionQuiter)
        self.menubar.addAction(self.menuMenu.menuAction())
        self.menubar.addAction(self.menuAide.menuAction())

        self.actionRecevoir.setEnabled(False)
        self.actionDemarer.setEnabled(True)
        
        self.retranslateUi(MainWindow)
        self.actionQuiter.triggered.connect(MainWindow.close)
        self.actionEntrer_mon_IP.triggered.connect(self.ouvrirmIP)
        self.actionConnecter_a_un_ami.triggered.connect(self.ouvrirsIP)
        self.actionDemarer.triggered.connect(self.DemarerServeur)
        self.actionRecevoir.triggered.connect(self.RecevoirC)
        QtCore.QMetaObject.connectSlotsByName(MainWindow)

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(_translate("MainWindow", "MainWindow", None))
        self.indicateur.setText(_translate("MainWindow", "               Non-Connecté", None))
        self.envoyer.setText(_translate("MainWindow", "envoyer", None))
        self.menuMenu.setTitle(_translate("MainWindow", "Menu", None))
        self.menuAide.setTitle(_translate("MainWindow", "Aide", None))
        self.actionEntrer_mon_IP.setText(_translate("MainWindow", "Entrer votre nom", None))
        self.actionConnecter_a_un_ami.setText(_translate("MainWindow", "Connecter a un ami", None))
        self.actionDemarer.setText(_translate("MainWindow", "Demarer le Serveur", None))
        self.actionRecevoir.setText(_translate("MainWindow", "Recevoir", None))
        self.actionQuiter.setText(_translate("MainWindow", "Quiter", None))


if __name__ == "__main__":
    connection=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    import sys
    conect=str()
    app = QtGui.QApplication(sys.argv)
    server_lance=False
    MainWindow = QtGui.QMainWindow()
    ui = Ui_MainWindow()
    ui.setupUi(MainWindow)
    MainWindow.show()
    sys.exit(app.exec_())

