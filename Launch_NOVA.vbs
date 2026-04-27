Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

projectRoot = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = projectRoot & "\.venv\Scripts\pythonw.exe"
appScript = projectRoot & "\backend\desktop_app.py"

If Not fso.FileExists(pythonw) Then
  MsgBox "Missing virtual environment pythonw at: " & pythonw, 16, "NOVA"
  WScript.Quit 1
End If

cmd = """" & pythonw & """ """ & appScript & """"
shell.Run cmd, 0, False
