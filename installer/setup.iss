; Inno Setup Script for SocialBidAI (--onedir + Edge WebView2 Runtime Guard)
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

#define MyAppName "SocialBidAI"
#define MyAppPublisher "SocialBidAI Studio"
#define MyAppExeName "SocialBidAI.exe"
#define WebView2ClientGuid "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

[Setup]
AppId={{8D4F69C2-4B73-4F2B-9A9D-92A76E21F011}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=SocialBidAI-Setup-v{#MyAppVersion}
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; WebView2 설치 시 불필요한 강제 재부팅 프롬프트 방지
RestartIfNeededByRun=no
UninstallDisplayIcon={app}\{#MyAppExeName}
; 설치 프로그램(setup.exe) 자체 아이콘
SetupIconFile=..\assets\icon\app.ico

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\SocialBidAI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; CI 파이프라인에서 다운로드하는 Microsoft Edge WebView2 Evergreen Bootstrapper
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsWebView2Install

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Microsoft Edge WebView2 런타임 설치 중..."; Flags: waituntilterminated; Check: NeedsWebView2Install
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
function IsWebView2Installed: Boolean;
var
  Version: String;
begin
  Result := False;
  if RegQueryStringValue(HKEY_LOCAL_MACHINE, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{#WebView2ClientGuid}', 'pv', Version) then
  begin
    if (Version <> '') and (Version <> '0.0.0.0') then
    begin
      Result := True;
      Exit;
    end;
  end;

  if RegQueryStringValue(HKEY_CURRENT_USER, 'Software\Microsoft\EdgeUpdate\Clients\{#WebView2ClientGuid}', 'pv', Version) then
  begin
    if (Version <> '') and (Version <> '0.0.0.0') then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

function NeedsWebView2Install: Boolean;
begin
  Result := not IsWebView2Installed;
end;
