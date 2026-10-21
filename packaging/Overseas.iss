#ifndef ProductVersion
  #define ProductVersion "4.0.3"
#endif
#ifndef PayloadBytes
  #define PayloadBytes "3233191118"
#endif

[Setup]
AppId={code:GetApplicationId}
AppName=Overseas
AppVersion={#ProductVersion}
VersionInfoVersion={#ProductVersion}.0
AppPublisher=Overseas
DefaultDirName={code:GetDefaultInstallDir}
DefaultGroupName=Overseas
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupArchitecture=x64
MinVersion=10.0
DisableWelcomePage=yes
DisableDirPage=no
DirExistsWarning=no
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableFinishedPage=yes
UsePreviousAppDir=yes
UsePreviousLanguage=no
CloseApplications=no
RestartApplications=no
AlwaysRestart=no
RestartIfNeededByRun=no
SetupMutex=Overseas.Factory.Installer
Uninstallable=yes
UninstallDisplayIcon={app}\app\Overseas.exe
OutputDir=output
OutputBaseFilename=Overseas-Full-Setup-{#ProductVersion}-x64
Compression=lzma2
SolidCompression=yes
LZMANumBlockThreads=1
WizardStyle=modern
SetupIconFile=assets\Overseas.ico
SetupLogging=yes

[Languages]
Name: "default"; MessagesFile: "compiler:Default.isl"

[Messages]
WizardSelectDir=选择安装位置
SelectDirDesc=选择 Overseas 的安装目录。
ButtonWizardBrowse=浏览…
ButtonBrowse=浏览…
BrowseDialogTitle=选择安装文件夹
BrowseDialogLabel=选择空间充足的文件夹，然后单击“确定”。
DiskSpaceGBLabel=程序至少需要 [gb] GB 空间，另请保留运行余量。
DiskSpaceMBLabel=程序至少需要 [mb] MB 空间，另请保留运行余量。
WizardPreparing=准备安装
PreparingDesc=正在检查安装条件。
SetupWindowTitle=Overseas 安装
WizardReady=准备安装 Overseas
ReadyLabel1=单击“安装”后，将自动安装并创建桌面和开始菜单入口。
ButtonInstall=安装
ButtonCancel=取消
ButtonNext=下一步 >
ButtonBack=< 返回
InstallingLabel=正在安装 Overseas，请稍候…
WizardInstalling=正在安装 Overseas
ConfirmUninstall=是否卸载 Overseas 程序？业务数据将保留。

[Files]
Source: "full-payload\*"; DestDir: "{app}"; Excludes: "*.pyc,*.log,*.sqlite3,*.db,*.dpapi,使用说明.txt,模型与分发说明.txt"; Flags: ignoreversion recursesubdirs createallsubdirs

Source: "overlay\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{code:GetDesktopDir}\Overseas"; Filename: "{app}\app\Overseas.exe"; Parameters: "--auto-port --config ""{app}\launch.json"""; WorkingDir: "{app}\app"
Name: "{code:GetMenuDir}\Overseas"; Filename: "{app}\app\Overseas.exe"; Parameters: "--auto-port --config ""{app}\launch.json"""; WorkingDir: "{app}\app"
Name: "{code:GetMenuDir}\卸载 Overseas"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\app\Overseas.exe"; Parameters: "--auto-port --config ""{app}\launch.json"""; WorkingDir: "{app}\app"; Flags: nowait skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\launch.json"
Type: files; Name: "{app}\.overseas-install.ini"
Type: filesandordirs; Name: "{app}\app\diagnostics"

[Code]
var DefaultNextClick: TNotifyEvent;

function TestRoot: String;
begin
  Result := ExpandConstant('{param:TESTROOT|}');
end;

function GetApplicationId(Param: String): String;
begin
  Result := 'Overseas.Factory';
  if TestRoot <> '' then
    Result := 'Overseas.Factory.QA.' + Copy(GetSHA256OfString(UTF8Encode(TestRoot)), 1, 20);
end;

function GetDefaultInstallDir(Param: String): String;
begin
  if TestRoot <> '' then Result := TestRoot + '\Program'
  else Result := ExpandConstant('{localappdata}\Programs\Overseas');
end;

function GetDataDir(Param: String): String;
begin
  if TestRoot <> '' then Result := TestRoot + '\Data\workspace'
  else Result := ExpandConstant('{localappdata}\Overseas\workspace');
end;

function GetDesktopDir(Param: String): String;
begin
  if TestRoot <> '' then Result := TestRoot + '\Desktop'
  else Result := ExpandConstant('{autodesktop}');
end;

function GetMenuDir(Param: String): String;
begin
  if TestRoot <> '' then Result := TestRoot + '\StartMenu\Overseas'
  else Result := ExpandConstant('{autoprograms}\Overseas');
end;

function InitializeSetup: Boolean;
var Marker: AnsiString;
begin
  Result := True;
  if TestRoot <> '' then begin
    Result := LoadStringFromFile(TestRoot + '\.overseas-installer-test', Marker) and
      (Trim(Marker) = 'Overseas isolated installer QA');
    if not Result then MsgBox('安装验收目录缺少专用标记，未进行安装。', mbError, MB_OK);
  end;
end;

function JsonPath(Value: String): String;
begin
  Result := Value;
  StringChangeEx(Result, '\', '\\', True);
end;

function WorkspaceRunning(DataDir: String): Boolean;
begin
  Result := FileExists(DataDir + '\runtime\desktop-instance.json');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if WorkspaceRunning(GetDataDir('')) then
    Result := '请先正常关闭此安装版 Overseas，再继续安装或升级。业务数据会保留。'
  else if FileExists(ExpandConstant('{app}\app\Overseas.exe')) and
    not FileExists(ExpandConstant('{app}\.overseas-install.ini')) then
    Result := '目标目录存在非本安装器管理的应用，未覆盖。请使用独立安装目录。';
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpSelectDir then begin
    WizardForm.NextButton.Caption := SetupMessage(msgButtonInstall);
    WizardForm.SelectDirLabel.Caption := '选择安装目录，单击“安装”自动创建桌面和开始菜单入口。可选择空间充足的其他盘。';
    WizardForm.SelectDirBrowseLabel.Caption := '已含 OCR/RAG 模型；建议预留 10GB，OCR 需 4GiB 可用内存。';
  end;
end;

function ValidateTarget: Boolean;
var Target, DataDir: String; FreeBytes, TotalBytes, Needed: Int64; Entry: TFindRec; Occupied: Boolean;
begin
  Result := True;
  Target := RemoveBackslashUnlessRoot(ExpandFileName(Trim(WizardForm.DirEdit.Text)));
  DataDir := AddBackslash(GetDataDir(''));
  if (Length(Target) < 4) or (ExtractFileDrive(Target) = '') or
    (CompareText(Target, ExtractFileDrive(Target) + '\') = 0) or
    (Pos(Uppercase(DataDir), Uppercase(AddBackslash(Target))) = 1) or
    (Pos(Uppercase(AddBackslash(Target)), Uppercase(DataDir)) = 1) then begin
    MsgBox('请选择独立的程序安装文件夹，不能使用磁盘根目录或业务数据目录。', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  if FileExists(Target + '\.overseas-install.ini') then begin
    if CompareText(GetIniString('Install', 'Workspace', '', Target + '\.overseas-install.ini'), GetDataDir('')) <> 0 then begin
      MsgBox('此目录属于另一个工作区，未覆盖。请选择独立目录。', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end else if DirExists(Target) then begin
    Occupied := False;
    if FindFirst(Target + '\*', Entry) then begin
      try
        repeat
          if (Entry.Name <> '.') and (Entry.Name <> '..') then Occupied := True;
        until not FindNext(Entry);
      finally
        FindClose(Entry);
      end;
    end;
    if Occupied then begin
      MsgBox('所选目录不是空目录，且没有本安装器记录。未覆盖任何文件，请选择新的安装目录。', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
  Needed := {#PayloadBytes} + 536870912;
  if not GetSpaceOnDisk64(ExtractFileDrive(Target), FreeBytes, TotalBytes) or (FreeBytes < Needed) then begin
    MsgBox('此盘空间不足，未开始安装。请选择空间充足的盘；建议至少10GB空闲。不会清理任何用户文件。', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  Needed := 536870912;
  if FileExists(Target + '\.overseas-install.ini') then Needed := {#PayloadBytes} + 536870912;
  if not GetSpaceOnDisk64(ExpandConstant('{tmp}'), FreeBytes, TotalBytes) or (FreeBytes < Needed) then begin
    MsgBox('Windows临时目录空间不足，未开始安装。请使用空间充足的临时目录重新运行；不会清理用户文件。', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  WizardForm.DirEdit.Text := Target;
end;

procedure GuardedNextClick(Sender: TObject);
begin
  if (WizardForm.CurPageID = wpSelectDir) and not ValidateTarget then Exit;
  DefaultNextClick(Sender);
end;

procedure InitializeWizard;
begin
  DefaultNextClick := WizardForm.NextButton.OnClick;
  WizardForm.NextButton.OnClick := @GuardedNextClick;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then Result := ValidateTarget;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Lines: TArrayOfString; DataDir: String;
begin
  if CurStep = ssPostInstall then begin
    DataDir := GetDataDir('');
    if not ForceDirectories(DataDir + '\runtime') then RaiseException('无法创建业务目录。');
    SetArrayLength(Lines, 1);
    Lines[0] := '{"protocol":"overseas-launch-v1","workspace":"' + JsonPath(DataDir) +
      '","website_extraction":"trafilatura","website_python":"app/components/extraction/runtime/python.exe",' +
      '"ocr_profile":"components/ocr/ocr-profile.json","rag":{' +
      '"local_python":"components/cpu/runtime/python.exe",' +
      '"embedding_dir":"components/cpu/models/e5-small",' +
      '"rerank_dir":"components/cpu/models/mmarco-minilm",' +
      '"local_model_cache":"' + JsonPath(DataDir + '\cache\rag') + '","local_timeout_seconds":120}}';
    if not SaveStringsToUTF8FileWithoutBOM(ExpandConstant('{app}\launch.json'), Lines, False) then
      RaiseException('无法保存启动配置。');
    if not FileExists(DataDir + '\runtime\desktop-setup.json') then begin
      Lines[0] := '{"dismissed":true}';
      if not SaveStringsToUTF8FileWithoutBOM(DataDir + '\runtime\desktop-setup.json', Lines, False) then
        RaiseException('无法保存安装入口状态。');
    end;
    if not SetIniString('Install', 'Workspace', DataDir, ExpandConstant('{app}\.overseas-install.ini')) then
      RaiseException('无法保存安装记录。');
    SetIniString('Install', 'Version', '{#ProductVersion}', ExpandConstant('{app}\.overseas-install.ini'));
  end;
end;

function InitializeUninstall: Boolean;
var DataDir: String;
begin
  DataDir := GetIniString('Install', 'Workspace', '', ExpandConstant('{app}\.overseas-install.ini'));
  Result := (DataDir = '') or not WorkspaceRunning(DataDir);
  if not Result then MsgBox('请先正常关闭此安装版 Overseas，再卸载。业务数据会保留。', mbError, MB_OK);
end;
