Option Explicit

Dim shell, fs, scriptDir, target
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
scriptDir = fs.GetParentFolderName(WScript.ScriptFullName)
target = fs.BuildPath(scriptDir, "check_bot_task.ps1")

' The one-minute health check must never create a foreground console window.
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & target & Chr(34), 0, False
