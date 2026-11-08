param([string]$Fixture, [string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$dialogue = Get-Content -LiteralPath $Fixture -Raw -Encoding UTF8 | ConvertFrom-Json
$voice = New-Object -ComObject SAPI.SpVoice
$registry = 'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens'
$installed = @(Get-ChildItem "Registry::$registry" -ErrorAction SilentlyContinue |
    Where-Object { $_.PSChildName -match 'ruRU' })
if (!$installed.Count) {
    $legacy = $voice.GetVoices('Language=419')
    if (!$legacy.Count) { throw 'Install a Russian Windows speech voice.' }
    $voice.Voice = $legacy.Item(0)
}
foreach ($turn in $dialogue.messages) {
    if ($installed.Count) {
        $index = if ($turn.speaker -eq 'client') { $installed.Count - 1 } else { 0 }
        $token = New-Object -ComObject SAPI.SpObjectToken
        $token.SetId($installed[$index].Name)
        $voice.Voice = $token
    }
    $stream = New-Object -ComObject SAPI.SpFileStream
    $stream.Format.Type = 22  # 22 kHz, 16 bit, mono
    $stream.Open((Join-Path $OutputDirectory ($turn.order.ToString() + '.wav')), 3)
    try {
        $voice.AudioOutputStream = $stream
        [void]$voice.Speak($turn.text, 16)
    } finally {
        $stream.Close()
    }
}
