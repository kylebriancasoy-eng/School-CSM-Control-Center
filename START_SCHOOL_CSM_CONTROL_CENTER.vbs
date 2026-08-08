Option Explicit
Dim fso, shell, base, command, dataRoot, appDataRoot, logFolder, logPath, logFile
On Error Resume Next

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
dataRoot = shell.SpecialFolders("MyDocuments") & "\MoSSLab Data"
appDataRoot = dataRoot & "\School CSM Control Center"
logFolder = appDataRoot & "\logs"
If Not fso.FolderExists(dataRoot) Then fso.CreateFolder dataRoot
If Not fso.FolderExists(appDataRoot) Then fso.CreateFolder appDataRoot
If Not fso.FolderExists(logFolder) Then fso.CreateFolder logFolder
logPath = logFolder & "\launcher.log"

Set logFile = fso.OpenTextFile(logPath, 8, True)
logFile.WriteLine ""
logFile.WriteLine "VBScript launcher started: " & Now
logFile.WriteLine "Application folder: " & base
logFile.Close

command = "cmd.exe /d /c """ & base & "\START_SCHOOL_CSM_CONTROL_CENTER_INTERNAL.cmd"""
shell.CurrentDirectory = base
shell.Run command, 0, False

If Err.Number <> 0 Then
    Set logFile = fso.OpenTextFile(logPath, 8, True)
    logFile.WriteLine "VBScript launcher error " & Err.Number & ": " & Err.Description
    logFile.Close
    MsgBox "The launcher failed. See MoSSLab Data\School CSM Control Center\logs\launcher.log.", 16, "School CSM Control Center"
End If
