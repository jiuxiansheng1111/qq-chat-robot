Option Explicit

Dim shell, fs, scriptDir, target
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
scriptDir = fs.GetParentFolderName(WScript.ScriptFullName)
target = fs.BuildPath(scriptDir, "run_bot.ps1")

' 0 = hidden window; False = do not wait for the long-running supervisor.
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & target & Chr(34), 0, False
