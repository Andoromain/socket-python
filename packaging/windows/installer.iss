; Installeur Windows (Inno Setup 6) : iscc /DAppVersion=2.0.0 packaging\windows\installer.iss
; Prérequis : avoir lancé PyInstaller (dossier dist\socket-chat).
; Installation par utilisateur, sans droits administrateur.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{7A0C4C55-2B8E-4C1E-9D7B-5E3C2B9F1A42}
AppName=Socket Chat
AppVersion={#AppVersion}
AppPublisher=Usage interne
DefaultDirName={autopf}\Socket Chat
DefaultGroupName=Socket Chat
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\dist
OutputBaseFilename=socket-chat-{#AppVersion}-windows-setup
SetupIconFile=..\..\src\socket_chat\resources\icon.ico
UninstallDisplayIcon={app}\socket-chat.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
; Mise à jour par-dessus une version existante : Socket Chat reste souvent actif dans la
; zone de notification. L'installeur le ferme lui-même (de force s'il ne répond pas),
; y compris en installation silencieuse.
CloseApplications=force
RestartApplications=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "firewall"; Description: "Autoriser les connexions entrantes dans le pare-feu Windows (nécessite les droits administrateur)"; Flags: unchecked; Check: IsAdminInstallMode

[Files]
Source: "..\..\dist\socket-chat\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Socket Chat"; Filename: "{app}\socket-chat.exe"
Name: "{autodesktop}\Socket Chat"; Filename: "{app}\socket-chat.exe"; Tasks: desktopicon

[Run]
; Règles recréées à chaque installation (mise à jour comprise) : pas de doublons.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Socket Chat"""; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Socket Chat"" dir=in action=allow program=""{app}\socket-chat.exe"" enable=yes"; Flags: runhidden; Tasks: firewall
; Appels : le son passe en UDP, par des ports variables, via le moteur de Chrome.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Socket Chat"" dir=in action=allow program=""{app}\_internal\PySide6\QtWebEngineProcess.exe"" enable=yes"; Flags: runhidden; Tasks: firewall
Filename: "{app}\socket-chat.exe"; Description: "{cm:LaunchProgram,Socket Chat}"; Flags: nowait postinstall skipifsilent
; Mise à jour automatique : l'application lance l'installeur avec /VERYSILENT /RELAUNCH=1
; puis se ferme ; elle est relancée une fois la nouvelle version installée.
Filename: "{app}\socket-chat.exe"; Flags: nowait runasoriginaluser; Check: ShouldRelaunch

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Socket Chat"""; Flags: runhidden; RunOnceId: "RemoveFirewallRule"; Check: IsAdminInstallMode

[Code]
function ShouldRelaunch: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:RELAUNCH|0}') = '1');
end;
