' Pinstock 원클릭 실행 (콘솔창 없음)
'   - 이 .vbs 와 같은 폴더에 .venv 가 있다고 가정
'   - 더블클릭하면 .venv\Scripts\pythonw.exe -m pinstock 으로 백그라운드 실행
'   - 바탕화면에 바로가기 만들어두면 더 편함

Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw  = scriptDir & "\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(pythonw) Then
    MsgBox "venv 를 찾을 수 없습니다:" & vbCrLf & pythonw & vbCrLf & vbCrLf & _
           "먼저 PowerShell 에서 다음을 실행하세요:" & vbCrLf & _
           "  python -m venv .venv" & vbCrLf & _
           "  .venv\Scripts\Activate.ps1" & vbCrLf & _
           "  pip install -r requirements.txt", _
           vbCritical, "Pinstock"
    WScript.Quit 1
End If

Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = scriptDir
' 0 = 창 숨김, False = 종료까지 기다리지 않음
sh.Run """" & pythonw & """ -m pinstock", 0, False
