<#

.SYNOPSIS
    Generates input data for SankeyMATIC based on Azure cost CSV data.
 
.DESCRIPTION
    Reads a CSV file, processes it, and generates formatted text output.
    Supports label overwrites via hardcoded defaults or an external JSON file.

.PARAMETER csvFile
    Path to the input CSV file. Mandatory.

.PARAMETER TopCategories
    The TOTAL number of categories to display in the middle column. (Default: 9)

.PARAMETER OverwriteLabels
    Optional path to a JSON file containing label mappings.
    Structure: { "Original Name": "New Name", ... }
    Values in this file will merge with or override the hardcoded defaults.

.PARAMETER Formatted
    If set, injects output into 'sankeymatic_template.txt'.

.PARAMETER Screen
    If set, outputs to console.
#>

 

param (
    [Parameter(Mandatory = $true)]
    [string]$csvFile,
    [int]$TopCategories = 9,
    [string]$OverwriteLabels,
    [switch]$Formatted,
    [switch]$Screen
)

# Helper function to get display label
function Get-Label {
    param($Name)
    if ($OverwriteMap.ContainsKey($Name)) { return $OverwriteMap[$Name] }
    return $Name
}


# ==========================================
# CONFIGURATION: OVERWRITE MAP
# ==========================================
# Initialize with Hardcoded Defaults
$OverwriteMap = @{
    "Azure Database for PostgreSQL" = "PostgreSQL"
    "Azure Cognitive Search"        = "Azure Search"
}

# 2. Merge/Override with External JSON if provided
if (-not [string]::IsNullOrWhiteSpace($OverwriteLabels)) {
    if (Test-Path $OverwriteLabels) {
        try {
            $jsonContent = Get-Content $OverwriteLabels -Raw | ConvertFrom-Json
            # Iterate through JSON properties and update the Hashtable
            $jsonContent.PSObject.Properties | ForEach-Object {
                $OverwriteMap[$_.Name] = $_.Value
            }
            Write-Host "Loaded overwrite map from '$OverwriteLabels'" -ForegroundColor Cyan
        }
        catch {
            Write-Warning "Failed to parse JSON file. Using defaults only. Error: $_"
        }
    }
    else {
        Write-Warning "JSON file '$OverwriteLabels' not found. Using defaults."
    }
}


# --- 1. Load Data ---
# Ensure input file exists
if (-not (Test-Path $csvFile)) {
    Write-Error "Input file '$csvFile' not found."
    exit 1
}

$data = Import-Csv $csvFile

# --- 2. Clean and Pre-process Data ---
$processedData = $data | ForEach-Object {
    $cost = $_."Monthy Cost" -replace ',', ''
    # Handle ETL Tools combination
    $category = $_.MeterCategory
    if ($category -eq 'Azure Data Factory v2' -or $category -eq 'Azure Synapse Analytics') {
        $category = 'ETL Tools'
    }

    [PSCustomObject]@{
        PricingModel  = $_.PricingModel
        MeterCategory = $category
        Environment   = $_.Environment
        MonthlyCost   = [math]::Round([double]$cost)
    }
}


# --- 3. Identify Categories to Keep ---
$groupedByCat = $processedData | Group-Object MeterCategory | Select-Object Name, @{N = 'TotalCost'; E = { ($_.Group | Measure-Object MonthlyCost -Sum).Sum } }

# 3a. Identify categories with Reservations (Mandatory)
$reservationCats = $processedData | Where-Object { $_.PricingModel -eq 'Reservation' } | Select-Object -ExpandProperty MeterCategory -Unique

# 3b. Determine Selection Logic
$totalUniqueCats = $groupedByCat.Count

if ($totalUniqueCats -le $TopCategories) {
    $categoriesToKeep = $groupedByCat.Name
}
else {
    # "Others" takes 1 slot
    $slotsForSpecific = $TopCategories - 1
    $mandatory = $reservationCats

    if ($mandatory.Count -ge $slotsForSpecific) {
        $categoriesToKeep = $mandatory
    }
    else {
        $slotsRemaining = $slotsForSpecific - $mandatory.Count
        $candidates = $groupedByCat | Where-Object { $mandatory -notcontains $_.Name } | Sort-Object TotalCost -Descending
        $fillers = $candidates | Select-Object -First $slotsRemaining -ExpandProperty Name
        $categoriesToKeep = @($mandatory) + @($fillers)
    }
}

# --- 4. Apply "Others" Grouping ---
$finalData = $processedData | ForEach-Object {
    $groupedCat = if ($categoriesToKeep -contains $_.MeterCategory) { $_.MeterCategory } else { "Others" }
    $_ | Add-Member -MemberType NoteProperty -Name "MeterCategory_Grouped" -Value $groupedCat -PassThru
}

# --- 5. Calculate Sort Orders (Descending by Cost) ---
$categorySortList = $finalData | Group-Object MeterCategory_Grouped |
Select-Object Name, @{N = 'TotalCost'; E = { ($_.Group | Measure-Object MonthlyCost -Sum).Sum } } |
Sort-Object TotalCost -Descending

$envSortList = $finalData | Group-Object Environment |

Select-Object Name, @{N = 'TotalCost'; E = { ($_.Group | Measure-Object MonthlyCost -Sum).Sum } } |
Sort-Object TotalCost -Descending

$pricingOrder = @('SavingsPlan', 'Reservation', 'OnDemand')


# --- 6. Generate Sankey Output String ---
$sb = [System.Text.StringBuilder]::new()


# Flow 1: PricingModel -> MeterCategory_Grouped
foreach ($model in $pricingOrder) {
    foreach ($catItem in $categorySortList) {
        $catName = $catItem.Name
        $sum = ($finalData | Where-Object { $_.PricingModel -eq $model -and $_.MeterCategory_Grouped -eq $catName } | Measure-Object MonthlyCost -Sum).Sum
        if ($sum -gt 0) {
            $label = Get-Label $catName
            [void]$sb.AppendLine("$model [$([math]::Round($sum))] $label")
        }
    }
}
[void]$sb.AppendLine("")

# Flow 2: MeterCategory_Grouped -> TotalMonthly
foreach ($catItem in $categorySortList) {
    $label = Get-Label $catItem.Name
    [void]$sb.AppendLine("$label [$([math]::Round($catItem.TotalCost))] TotalMonthly")
}
[void]$sb.AppendLine("")

# Flow 3: TotalMonthly -> Environment
foreach ($envItem in $envSortList) {
    $label = Get-Label $envItem.Name
    [void]$sb.AppendLine("TotalMonthly [$([math]::Round($envItem.TotalCost))] $label")
}

$sankeyOutput = $sb.ToString()

# --- 7. Handle Outputs ---
# Output to Screen
if ($Screen) {
    Write-Output $sankeyOutput
}

# Output to File (Formatted)
if ($Formatted) {
    $templateFile = "sankeymatic_template.txt"
    if (Test-Path $templateFile) {
        $templateContent = Get-Content $templateFile -Raw
        $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        $fileTimestamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $outputFilename = "sankeymatic_$fileTimestamp.txt"

        # Replace Timestamp
        $templateContent = $templateContent -replace "%GENERATED DATETIME%", $timestamp

        # Insert into Nodes and Flows section
        $marker = "// === Nodes and Flows ==="
        if ($templateContent.Contains($marker)) {
            $replacement = "$marker`r`n$sankeyOutput"
            $finalContent = $templateContent.Replace($marker, $replacement)

            Set-Content -Path $outputFilename -Value $finalContent
            Write-Host "Successfully created formatted file: $outputFilename" -ForegroundColor Green
        }
        else {
            Write-Warning "Template file found but '$marker' section missing. File not saved."
        }
    }
    else {
        Write-Warning "Template file '$templateFile' not found in current directory. Skipping file generation."
    }
}