$ErrorActionPreference = "Continue"
$testsDir = "C:\Users\lichengjun\Desktop\edge-visionQA\tests"
$python = "C:\Users\lichengjun\AppData\Local\Programs\Python\Python313\python.exe"
$outputFile = Join-Path $testsDir "_runner_output.txt"

function Write-Line($text) {
    Add-Content -Path $outputFile -Value $text
    Write-Host $text
}

Write-Line ("=" * 60)
Write-Line "  edge-visionQA Test Runner"
Write-Line "  Starting simulate_log.py + run_validation.py"
Write-Line ("=" * 60)
Write-Line ""

# Step 1
Write-Line ("=" * 60)
Write-Line "  Step 1: Running simulate_log.py"
Write-Line ("=" * 60)

$result1 = & $python (Join-Path $testsDir "simulate_log.py") 2>&1
Write-Line $result1
Write-Line "(exit code: $LASTEXITCODE)"
Write-Line ""

# Step 2
Write-Line ("=" * 60)
Write-Line "  Step 2: Running run_validation.py"
Write-Line ("=" * 60)

$result2 = & $python (Join-Path $testsDir "run_validation.py") 2>&1
Write-Line $result2
Write-Line "(exit code: $LASTEXITCODE)"
Write-Line ""

Write-Line ("=" * 60)
Write-Line "  Both scripts completed"
Write-Line ("=" * 60)
