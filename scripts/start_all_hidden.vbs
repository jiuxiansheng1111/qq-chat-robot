Option Explicit

Dim fso
Dim shell
Dim scriptDir
Dim psScript
Dim psArgs

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
psScript = fso.BuildPath(scriptDir, "start_all.ps1")
psArgs = "-NoProfile -ExecutionPolicy Bypass -File " & Chr(34) & psScript & Chr(34)

Call shell.Run("powershell.exe " & psArgs, 0, False)
