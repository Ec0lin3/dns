<#
.SYNOPSIS
    Find sensitive AD groups (by name patterns), resolve nested membership,
    and find who holds configurable dangerous rights (Reset Password, GenericAll, etc.).

.DESCRIPTION
    Driven entirely by an external JSON config (see config.example.json).
      1. Finds groups whose name matches any pattern in config.groupNamePatterns.
      2. Recursively expands membership (nested groups) in BOTH directions:
         - members  : who is effectively inside each sensitive group (incl. nested).
         - memberOf : which sensitive groups are nested inside other groups.
      3. Scans object ACLs and reports every ACE that grants one of the
         config.sensitiveRights to one of the sensitive principals
         (the matched groups + their nested members).

    On-Prem only. For Entra ID use Find-SensitiveEntraGroups.ps1.

.PARAMETER ConfigPath
    Path to config json. Default: ./config.json

.PARAMETER SkipAcl
    Skip the (slower) ACL scan; only do group + nesting discovery.

.EXAMPLE
    .\Find-SensitiveADGroups.ps1 -ConfigPath .\config.json

.NOTES
    Run from a domain-joined host with RSAT (ActiveDirectory module) and read
    access to AD. No elevation required for read-only auditing.
#>
[CmdletBinding()]
param(
    [string]$ConfigPath = "$PSScriptRoot\config.json",
    [switch]$SkipAcl,
    [switch]$NoOpen
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ----------------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------------
if (-not (Test-Path $ConfigPath)) {
    $exampleP = Join-Path $PSScriptRoot 'config.example.json'
    if (Test-Path $exampleP) {
        Copy-Item $exampleP $ConfigPath
        Write-Host "[+] No config found. Created '$ConfigPath' from config.example.json. Edit it anytime to customize." -ForegroundColor Yellow
    } else {
        $defaultConfig = @'
{
  "_comment": "AD-SensitiveAudit config (auto-generated default). Edit to customize. All sections optional except groupNamePatterns.",
  "onprem": {
    "server": "",
    "searchBase": "",
    "aclScanBase": ""
  },
  "entra": {
    "tenantId": "",
    "passwordResetRoleTemplates": [
      "62e90394-69f5-4237-9190-012177145e10",
      "fe930be7-5e62-47db-91af-98c3a49a38b1",
      "729827e3-9c14-49f7-bb1b-9608f156bbb8",
      "966707d0-3269-4727-9be2-8c3a10f19b9d"
    ],
    "_roleTemplateNames": {
      "62e90394-69f5-4237-9190-012177145e10": "Global Administrator",
      "fe930be7-5e62-47db-91af-98c3a49a38b1": "User Administrator",
      "729827e3-9c14-49f7-bb1b-9608f156bbb8": "Helpdesk Administrator",
      "966707d0-3269-4727-9be2-8c3a10f19b9d": "Password Administrator"
    }
  },
  "groupNamePatterns": [
    "*admin*", "*administrator*", "Domain Admins", "Enterprise Admins", "Schema Admins",
    "Administrators", "Account Operators", "Backup Operators", "Server Operators",
    "Print Operators", "DnsAdmins", "Group Policy Creator Owners",
    "*priv*", "*tier0*", "*-DA", "*helpdesk*", "*passwordreset*"
  ],
  "sensitiveRights": [
    { "name": "Reset Password", "type": "ExtendedRight", "guid": "00299570-246d-11d0-a768-00aa006e0529" },
    { "name": "Change Password", "type": "ExtendedRight", "guid": "ab721a53-1e2f-11d0-9819-00aa0040529b" },
    { "name": "Write Membership (member attr)", "type": "WriteProperty", "guid": "bf9679c0-0de6-11d0-a285-00aa003049e2" },
    { "name": "All Validated Writes / Self-Membership", "type": "Self", "guid": "bf9679c0-0de6-11d0-a285-00aa003049e2" },
    { "name": "Full Control", "type": "ActiveDirectoryRight", "right": "GenericAll" },
    { "name": "Write DACL (can grant self any right)", "type": "ActiveDirectoryRight", "right": "WriteDacl" },
    { "name": "Write Owner (can take ownership)", "type": "ActiveDirectoryRight", "right": "WriteOwner" },
    { "name": "Generic Write", "type": "ActiveDirectoryRight", "right": "GenericWrite" },
    { "name": "All Extended Rights", "type": "ActiveDirectoryRight", "right": "ExtendedRight" }
  ],
  "shadowAdmin": {
    "controlRights": ["GenericAll", "WriteDacl", "WriteOwner", "GenericWrite", "WriteMembership", "Self"],
    "excludeWellKnown": true,
    "excludeSids": []
  },
  "output": {
    "folder": "./output",
    "csv": true,
    "html": true
  }
}
'@
        $defaultConfig | Out-File -FilePath $ConfigPath -Encoding UTF8
        Write-Host "[+] No config found. Wrote a standard default config to '$ConfigPath'. Edit it anytime to customize." -ForegroundColor Yellow
    }
}
$cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json

Import-Module ActiveDirectory -ErrorAction Stop

$server     = if ($cfg.onprem.server)     { $cfg.onprem.server }     else { (Get-ADDomainController -Discover -ErrorAction SilentlyContinue).HostName | Select-Object -First 1 }
$searchBase = if ($cfg.onprem.searchBase) { $cfg.onprem.searchBase } else { (Get-ADDomain).DistinguishedName }
$adParams   = @{}
if ($server) { $adParams['Server'] = "$server" }

$outFolder = if ($cfg.output.folder) { $cfg.output.folder } else { "$PSScriptRoot\output" }
if (-not (Test-Path $outFolder)) { New-Item -ItemType Directory -Path $outFolder -Force | Out-Null }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

function Write-Step($m) { Write-Host "[*] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[+] $m" -ForegroundColor Green }
function Write-Warn2($m){ Write-Host "[!] $m" -ForegroundColor Yellow }

function ConvertTo-HtmlEnc([string]$s) {
    if ($null -eq $s) { return '' }
    $s.Replace('&','&amp;').Replace('<','&lt;').Replace('>','&gt;').Replace('"','&quot;')
}

# Build an HTML <table> from a collection of objects, picking the given columns.
function New-HtmlTable {
    param([object[]]$Rows, [string[]]$Columns, [hashtable]$RowClass)
    if (-not $Rows -or $Rows.Count -eq 0) { return '<p class="empty">No findings.</p>' }
    $sb = [System.Text.StringBuilder]::new()
    [void]$sb.Append('<table><thead><tr>')
    foreach ($c in $Columns) { [void]$sb.Append("<th>$(ConvertTo-HtmlEnc $c)</th>") }
    [void]$sb.Append('</tr></thead><tbody>')
    foreach ($r in $Rows) {
        $isHot = $false
        if ($RowClass) { $isHot = [bool]($RowClass.Test.Invoke($r) | Select-Object -First 1) }
        $cls = if ($isHot) { " class=`"$($RowClass.Class)`"" } else { '' }
        [void]$sb.Append("<tr$cls>")
        foreach ($c in $Columns) {
            $val = $r.$c
            if ($val -is [bool]) { $val = if ($val) { 'Yes' } else { 'No' } }
            [void]$sb.Append("<td>$(ConvertTo-HtmlEnc ([string]$val))</td>")
        }
        [void]$sb.Append('</tr>')
    }
    [void]$sb.Append('</tbody></table>')
    $sb.ToString()
}

Write-Step "Domain : $((Get-ADDomain @adParams).DNSRoot)"
Write-Step "Server : $server"
Write-Step "Base   : $searchBase"

# ----------------------------------------------------------------------------
# 1. Find sensitive groups by name pattern
# ----------------------------------------------------------------------------
Write-Step "Matching groups against $($cfg.groupNamePatterns.Count) name pattern(s)..."

$allGroups = Get-ADGroup -Filter * -SearchBase $searchBase -Properties member, memberOf, sAMAccountName, objectSid @adParams
$patterns  = $cfg.groupNamePatterns

$sensitiveGroups = foreach ($g in $allGroups) {
    foreach ($p in $patterns) {
        if ($g.Name -like $p -or $g.sAMAccountName -like $p) {
            $g | Add-Member -NotePropertyName MatchedPattern -NotePropertyValue $p -Force
            $g; break
        }
    }
}
$sensitiveGroups = @($sensitiveGroups)
Write-Ok "Found $($sensitiveGroups.Count) sensitive group(s)."

# ----------------------------------------------------------------------------
# 2. Nested membership expansion (both directions)
# ----------------------------------------------------------------------------
Write-Step "Expanding nested membership (recursive)..."

# 2a. Effective MEMBERS of each sensitive group (who is inside, incl. nested groups)
$memberRows   = New-Object System.Collections.Generic.List[object]
$sensitiveSids = New-Object System.Collections.Generic.HashSet[string]
$groupMemberSids = @{}   # groupDN -> HashSet[string] of effective member SIDs (for shadow-admin check)

foreach ($grp in $sensitiveGroups) {
    [void]$sensitiveSids.Add($grp.objectSid.Value)
    $perGroup = New-Object System.Collections.Generic.HashSet[string]
    [void]$perGroup.Add($grp.objectSid.Value)
    # Recursive members via LDAP_MATCHING_RULE_IN_CHAIN (1.2.840.113556.1.4.1941)
    $members = Get-ADObject -LDAPFilter "(memberOf:1.2.840.113556.1.4.1941:=$($grp.DistinguishedName))" `
                            -Properties objectClass, sAMAccountName, objectSid @adParams
    foreach ($m in $members) {
        if ($m.objectSid) { [void]$sensitiveSids.Add($m.objectSid.Value); [void]$perGroup.Add($m.objectSid.Value) }
        $memberRows.Add([pscustomobject]@{
            SensitiveGroup = $grp.Name
            MatchedPattern = $grp.MatchedPattern
            MemberName     = $m.sAMAccountName
            MemberClass    = $m.objectClass
            MemberDN       = $m.DistinguishedName
            Relation       = 'effective-member (nested)'
        })
    }
    $groupMemberSids[$grp.DistinguishedName] = $perGroup
}

# 2b. Which sensitive groups are NESTED INSIDE other groups (parent chain)
$nestingRows = New-Object System.Collections.Generic.List[object]
foreach ($grp in $sensitiveGroups) {
    $parents = Get-ADGroup -LDAPFilter "(member:1.2.840.113556.1.4.1941:=$($grp.DistinguishedName))" `
                           -Properties name @adParams
    foreach ($par in $parents) {
        $isParentSensitive = $sensitiveGroups.DistinguishedName -contains $par.DistinguishedName
        $nestingRows.Add([pscustomobject]@{
            SensitiveGroup   = $grp.Name
            NestedInside     = $par.Name
            ParentDN         = $par.DistinguishedName
            ParentSensitive  = $isParentSensitive
        })
    }
}
Write-Ok "Effective members: $($memberRows.Count) | Nesting links: $($nestingRows.Count) | Unique sensitive principals (incl. nested): $($sensitiveSids.Count)"

# ----------------------------------------------------------------------------
# 3. ACL scan — who holds the configured dangerous rights
# ----------------------------------------------------------------------------
$aclRows = New-Object System.Collections.Generic.List[object]

if (-not $SkipAcl) {
    $aclBase = if ($cfg.onprem.aclScanBase) { $cfg.onprem.aclScanBase } else { $searchBase }
    Write-Step "Scanning ACLs under: $aclBase  (this can take a while)..."

    # Pre-index configured rights
    $extGuidMap = @{}   # guid(lower) -> right name, for ExtendedRight/WriteProperty/Self
    $adRightFlags = @() # ActiveDirectoryRights flag names to flag outright
    foreach ($r in $cfg.sensitiveRights) {
        switch ($r.type) {
            'ActiveDirectoryRight' { $adRightFlags += [pscustomobject]@{ Name = $r.name; Flag = $r.right } }
            default { if ($r.guid) { $extGuidMap[$r.guid.ToLower()] = $r.name } }
        }
    }
    $zeroGuid = '00000000-0000-0000-0000-000000000000'

    # Build SID -> friendly label for sensitive principals (for fast ACE matching)
    $sidLabel = @{}
    foreach ($g in $sensitiveGroups) { $sidLabel[$g.objectSid.Value] = "$($g.Name) (group)" }

    # Objects to inspect: users, groups, computers, OUs under the base
    $targets = Get-ADObject -LDAPFilter '(|(objectClass=user)(objectClass=group)(objectClass=computer)(objectClass=organizationalUnit))' `
                            -SearchBase $aclBase -Properties nTSecurityDescriptor, objectClass @adParams
    Write-Step "Inspecting ACLs on $($targets.Count) object(s)..."

    $i = 0
    foreach ($obj in $targets) {
        $i++
        if ($i % 500 -eq 0) { Write-Host "    ...$i / $($targets.Count)" -ForegroundColor DarkGray }
        $sd = $obj.nTSecurityDescriptor
        if (-not $sd) { continue }
        foreach ($ace in $sd.Access) {
            if ($ace.AccessControlType -ne 'Allow') { continue }

            # Resolve ACE trustee to SID
            $aceSid = $null
            try {
                $aceSid = if ($ace.IdentityReference -is [System.Security.Principal.SecurityIdentifier]) {
                    $ace.IdentityReference.Value
                } else {
                    ([System.Security.Principal.NTAccount]$ace.IdentityReference).Translate([System.Security.Principal.SecurityIdentifier]).Value
                }
            } catch { continue }

            if (-not $sensitiveSids.Contains($aceSid)) { continue }   # only ACEs granting to sensitive principals

            $rights    = $ace.ActiveDirectoryRights.ToString()
            $objType   = "$($ace.ObjectType)".ToLower()
            $matchedRight = $null

            # a) ActiveDirectoryRights flag matches (GenericAll/WriteDacl/WriteOwner/GenericWrite/ExtendedRight)
            foreach ($f in $adRightFlags) {
                if ($rights -match $f.Flag) { $matchedRight = $f.Name; break }
            }
            # b) Specific extended-right / property GUID matches
            if (-not $matchedRight -and $extGuidMap.ContainsKey($objType)) {
                $matchedRight = $extGuidMap[$objType]
            }
            # c) Right that applies to ALL (objectType = all-zeros) while holding ExtendedRight/WriteProperty
            if (-not $matchedRight -and $objType -eq $zeroGuid -and ($rights -match 'ExtendedRight|WriteProperty|GenericAll')) {
                $matchedRight = "ALL ($rights)"
            }

            if ($matchedRight) {
                $label = if ($sidLabel.ContainsKey($aceSid)) { $sidLabel[$aceSid] } else { $aceSid }
                $aclRows.Add([pscustomobject]@{
                    TargetObject = $obj.Name
                    TargetClass  = $obj.objectClass
                    TargetDN     = $obj.DistinguishedName
                    GrantedTo    = $label
                    GrantedToSid = $aceSid
                    MatchedRight = $matchedRight
                    AdRights     = $rights
                    Inherited    = $ace.IsInherited
                })
            }
        }
    }
    Write-Ok "ACL findings: $($aclRows.Count)"
} else {
    Write-Warn2 "ACL scan skipped (-SkipAcl)."
}

# ----------------------------------------------------------------------------
# 3.5 Shadow Admins — control over a sensitive GROUP object without membership
# ----------------------------------------------------------------------------
Write-Step "Hunting shadow admins (control over sensitive groups without membership)..."

$shadowRows = New-Object System.Collections.Generic.List[object]
$memberGuid = 'bf9679c0-0de6-11d0-a285-00aa003049e2'   # 'member' attr / self-membership
$zeroGuid2  = '00000000-0000-0000-0000-000000000000'

# Read shadowAdmin config defensively (works even if the section is absent)
$shadowCfg  = if ($cfg.PSObject.Properties.Name -contains 'shadowAdmin') { $cfg.shadowAdmin } else { $null }
$ctrl       = if ($shadowCfg -and $shadowCfg.controlRights) { @($shadowCfg.controlRights) } else { @('GenericAll','WriteDacl','WriteOwner','GenericWrite','WriteMembership','Self') }
$excludeWk  = if ($shadowCfg -and $null -ne $shadowCfg.excludeWellKnown) { [bool]$shadowCfg.excludeWellKnown } else { $true }
$extraSids  = if ($shadowCfg -and $shadowCfg.excludeSids) { @($shadowCfg.excludeSids) } else { @() }

# Build the set of trustee SIDs to ignore (legitimately privileged)
$excludeSids = New-Object System.Collections.Generic.HashSet[string]
foreach ($s in $extraSids) { if ($s) { [void]$excludeSids.Add("$s") } }
if ($excludeWk) {
    $domSid = (Get-ADDomain @adParams).DomainSID.Value
    foreach ($rid in 512,519,518,516,520) { [void]$excludeSids.Add("$domSid-$rid") }  # DA, EA, Schema, DCs, GPCO
    foreach ($w in 'S-1-5-18','S-1-5-32-544','S-1-5-32-548','S-1-5-9','S-1-5-10','S-1-3-0','S-1-5-32-549','S-1-5-32-550') {
        [void]$excludeSids.Add($w)  # SYSTEM, Administrators, Account Ops, EntDCs, SELF, CreatorOwner, Server/Print Ops
    }
}

foreach ($grp in $sensitiveGroups) {
    $sd = (Get-ADObject -Identity $grp.DistinguishedName -Properties nTSecurityDescriptor @adParams).nTSecurityDescriptor
    if (-not $sd) { continue }
    $memberSet = $groupMemberSids[$grp.DistinguishedName]
    foreach ($ace in $sd.Access) {
        if ($ace.AccessControlType -ne 'Allow') { continue }

        # Which control right does this ACE grant over the group object?
        $rights  = $ace.ActiveDirectoryRights.ToString()
        $objType = "$($ace.ObjectType)".ToLower()
        $hit = $null
        if ($ctrl -contains 'GenericAll'    -and $rights -match 'GenericAll')   { $hit = 'GenericAll' }
        elseif ($ctrl -contains 'WriteDacl'  -and $rights -match 'WriteDacl')    { $hit = 'WriteDacl' }
        elseif ($ctrl -contains 'WriteOwner' -and $rights -match 'WriteOwner')   { $hit = 'WriteOwner' }
        elseif ($ctrl -contains 'GenericWrite' -and $rights -match 'GenericWrite') { $hit = 'GenericWrite' }
        elseif ($ctrl -contains 'WriteMembership' -and $rights -match 'WriteProperty' -and ($objType -eq $memberGuid -or $objType -eq $zeroGuid2)) { $hit = 'WriteMembership' }
        elseif ($ctrl -contains 'Self' -and $rights -match 'Self' -and ($objType -eq $memberGuid -or $objType -eq $zeroGuid2)) { $hit = 'Self (add-self-as-member)' }
        if (-not $hit) { continue }

        # Resolve trustee SID
        $aceSid = $null
        try {
            $aceSid = if ($ace.IdentityReference -is [System.Security.Principal.SecurityIdentifier]) {
                $ace.IdentityReference.Value
            } else {
                ([System.Security.Principal.NTAccount]$ace.IdentityReference).Translate([System.Security.Principal.SecurityIdentifier]).Value
            }
        } catch { continue }

        if ($excludeSids.Contains($aceSid)) { continue }          # legitimately privileged
        if ($memberSet -and $memberSet.Contains($aceSid)) { continue }  # already a member -> not "shadow"

        # Friendly name for the trustee
        $who = "$($ace.IdentityReference)"
        try { $who = (Get-ADObject -Filter "objectSid -eq '$aceSid'" -Properties sAMAccountName @adParams).sAMAccountName } catch {}
        if (-not $who) { $who = "$($ace.IdentityReference)" }

        $shadowRows.Add([pscustomobject]@{
            ShadowAdmin   = $who
            ShadowAdminSid= $aceSid
            ControlRight  = $hit
            OverGroup     = $grp.Name
            OverGroupDN   = $grp.DistinguishedName
            Inherited     = $ace.IsInherited
        })
    }
}
Write-Ok "Shadow admin findings: $($shadowRows.Count)"

# ----------------------------------------------------------------------------
# 4. Output
# ----------------------------------------------------------------------------
Write-Step "Writing reports to $outFolder ..."

$files = @{
    Groups   = Join-Path $outFolder "sensitive-groups_$stamp.csv"
    Members  = Join-Path $outFolder "nested-members_$stamp.csv"
    Nesting  = Join-Path $outFolder "group-nesting_$stamp.csv"
    Acl      = Join-Path $outFolder "acl-findings_$stamp.csv"
    Shadow   = Join-Path $outFolder "shadow-admins_$stamp.csv"
}

$groupRows = $sensitiveGroups | Select-Object Name, sAMAccountName, MatchedPattern, DistinguishedName
$groupRows   | Export-Csv $files.Groups  -NoTypeInformation -Encoding UTF8
$memberRows  | Export-Csv $files.Members -NoTypeInformation -Encoding UTF8
$nestingRows | Export-Csv $files.Nesting -NoTypeInformation -Encoding UTF8
$shadowRows  | Export-Csv $files.Shadow  -NoTypeInformation -Encoding UTF8
if (-not $SkipAcl) { $aclRows | Export-Csv $files.Acl -NoTypeInformation -Encoding UTF8 }

# ---- HTML report -----------------------------------------------------------
if ($cfg.output.html) {
    $htmlPath = Join-Path $outFolder "report_$stamp.html"
    $domainName = (Get-ADDomain @adParams).DNSRoot
    $generated  = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $aclCount   = if ($SkipAcl) { 'skipped' } else { "$($aclRows.Count)" }
    $nonInherited = if (-not $SkipAcl) { @($aclRows | Where-Object { -not $_.Inherited }) } else { @() }

    # Sections
    $tblGroups  = New-HtmlTable -Rows $groupRows   -Columns 'Name','sAMAccountName','MatchedPattern','DistinguishedName'
    $tblMembers = New-HtmlTable -Rows $memberRows  -Columns 'SensitiveGroup','MemberName','MemberClass','Relation','MemberDN'
    $nestClass  = @{ Test = { param($r) $r.ParentSensitive }; Class = 'hot' }
    $tblNesting = New-HtmlTable -Rows $nestingRows -Columns 'SensitiveGroup','NestedInside','ParentSensitive','ParentDN' -RowClass $nestClass

    if ($SkipAcl) {
        $tblAcl = '<p class="empty">ACL scan skipped (-SkipAcl).</p>'
    } else {
        $aclClass = @{ Test = { param($r) -not $r.Inherited }; Class = 'hot' }
        $tblAcl = New-HtmlTable -Rows ($aclRows | Sort-Object Inherited, MatchedRight) `
                    -Columns 'GrantedTo','MatchedRight','TargetObject','TargetClass','AdRights','Inherited' -RowClass $aclClass
    }

    # Shadow admins: every row is noteworthy; flag direct (non-inherited) as hottest
    $shadowClass = @{ Test = { param($r) -not $r.Inherited }; Class = 'hot' }
    $tblShadow   = New-HtmlTable -Rows ($shadowRows | Sort-Object Inherited, OverGroup) `
                    -Columns 'ShadowAdmin','ControlRight','OverGroup','Inherited','OverGroupDN' -RowClass $shadowClass

    $html = @"
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AD Sensitive Audit - $(ConvertTo-HtmlEnc $domainName)</title>
<style>
  :root { --bg:#0f1419; --card:#1a2230; --ink:#e6edf3; --muted:#8b98a9; --line:#2b3648;
          --accent:#4ea1ff; --hot:#ff5d5d; --hotbg:#3a1d22; --green:#3fb950; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
  header { padding:28px 32px; border-bottom:1px solid var(--line);
           background:linear-gradient(180deg,#141b26,#0f1419); }
  header h1 { margin:0 0 6px; font-size:22px; }
  header .meta { color:var(--muted); font-size:13px; }
  .wrap { padding:24px 32px; max-width:1400px; margin:0 auto; }
  .cards { display:flex; gap:16px; flex-wrap:wrap; margin-bottom:28px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:18px 22px; min-width:170px; }
  .card .n { font-size:30px; font-weight:700; }
  .card .l { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.5px; }
  .card.alert .n { color:var(--hot); }
  section { margin-bottom:34px; }
  section h2 { font-size:16px; border-left:3px solid var(--accent); padding-left:10px; margin:0 0 12px; }
  table { width:100%; border-collapse:collapse; background:var(--card);
          border:1px solid var(--line); border-radius:10px; overflow:hidden; font-size:13px; }
  th { text-align:left; background:#141c28; color:var(--muted); padding:9px 12px;
       font-weight:600; position:sticky; top:0; }
  td { padding:8px 12px; border-top:1px solid var(--line); word-break:break-word; }
  tr.hot td { background:var(--hotbg); }
  tr.hot td:nth-child(2), tr.hot td:first-child { color:#ffb3b3; font-weight:600; }
  .empty { color:var(--muted); font-style:italic; padding:10px 0; }
  .legend { color:var(--muted); font-size:12px; margin:-6px 0 14px; }
  .tag { display:inline-block; background:var(--hotbg); color:#ffb3b3; border:1px solid #5a2b30;
         padding:1px 7px; border-radius:6px; font-size:11px; }
  footer { color:var(--muted); font-size:12px; padding:20px 32px; border-top:1px solid var(--line); }
</style>
</head>
<body>
<header>
  <h1>AD Sensitive Groups &amp; Permissions Audit</h1>
  <div class="meta">Domain: <b>$(ConvertTo-HtmlEnc $domainName)</b> &nbsp;|&nbsp; Base: $(ConvertTo-HtmlEnc $searchBase) &nbsp;|&nbsp; Generated: $generated</div>
</header>
<div class="wrap">
  <div class="cards">
    <div class="card"><div class="n">$($sensitiveGroups.Count)</div><div class="l">Sensitive Groups</div></div>
    <div class="card"><div class="n">$($memberRows.Count)</div><div class="l">Nested Members</div></div>
    <div class="card"><div class="n">$($nestingRows.Count)</div><div class="l">Nesting Links</div></div>
    <div class="card $(if(-not $SkipAcl -and $nonInherited.Count){'alert'})"><div class="n">$aclCount</div><div class="l">ACL Findings</div></div>
    <div class="card $(if($nonInherited.Count){'alert'})"><div class="n">$($nonInherited.Count)</div><div class="l">Direct (non-inherited)</div></div>
    <div class="card $(if($shadowRows.Count){'alert'})"><div class="n">$($shadowRows.Count)</div><div class="l">Shadow Admins</div></div>
  </div>

  <section>
    <h2>1. Sensitive Groups (matched by name)</h2>
    $tblGroups
  </section>

  <section>
    <h2>2. Nested / Effective Members</h2>
    <div class="legend">Who is effectively inside each sensitive group, including via nested groups (recursive).</div>
    $tblMembers
  </section>

  <section>
    <h2>3. Group Nesting</h2>
    <div class="legend">Which groups each sensitive group is nested inside. <span class="tag">red = parent is also sensitive</span></div>
    $tblNesting
  </section>

  <section>
    <h2>4. ACL Findings &mdash; dangerous rights held by sensitive principals</h2>
    <div class="legend">Each row = an ACE granting a configured right (e.g. Reset Password) to a sensitive group or its nested member. <span class="tag">red = directly assigned (not inherited)</span></div>
    $tblAcl
  </section>

  <section>
    <h2>5. Shadow Admins &mdash; control over a sensitive group without membership</h2>
    <div class="legend">Principals who can take over a sensitive group (GenericAll / WriteDacl / WriteOwner / write membership) but are NOT members &mdash; they can add themselves. Built-in privileged trustees excluded. <span class="tag">red = directly assigned (not inherited)</span></div>
    $tblShadow
  </section>
</div>
<footer>AD-SensitiveAudit &mdash; read-only audit. Review red rows first.</footer>
</body>
</html>
"@
    $html | Out-File -FilePath $htmlPath -Encoding UTF8
    $files['Html'] = $htmlPath
}

Write-Host ""
Write-Ok "DONE."
Write-Host "  Sensitive groups : $($sensitiveGroups.Count)"  -ForegroundColor White
Write-Host "  Nested members   : $($memberRows.Count)"        -ForegroundColor White
Write-Host "  Nesting links    : $($nestingRows.Count)"       -ForegroundColor White
if (-not $SkipAcl) { Write-Host "  ACL findings     : $($aclRows.Count)" -ForegroundColor White }
Write-Host "  Shadow admins    : $($shadowRows.Count)" -ForegroundColor $(if ($shadowRows.Count) { 'Red' } else { 'White' })
Write-Host ""
Write-Host "Reports:" -ForegroundColor White
$files.GetEnumerator() | ForEach-Object { if (Test-Path $_.Value) { Write-Host "  $($_.Value)" -ForegroundColor Gray } }
if ($files.ContainsKey('Html') -and (Test-Path $files.Html)) {
    Write-Host "`nHTML report -> $($files.Html)" -ForegroundColor Green
    # Open in default browser unless -NoOpen (use -NoOpen for headless / scheduled runs)
    if (-not $NoOpen) { try { Start-Process $files.Html } catch {} }
}

# Quick on-screen highlight of the scariest findings
if (-not $SkipAcl -and $aclRows.Count) {
    Write-Host "`nTop ACL findings (non-inherited):" -ForegroundColor Yellow
    $aclRows | Where-Object { -not $_.Inherited } |
        Select-Object GrantedTo, MatchedRight, TargetObject, TargetClass -First 25 |
        Format-Table -AutoSize
}
if ($shadowRows.Count) {
    Write-Host "SHADOW ADMINS (control over sensitive groups, not members):" -ForegroundColor Red
    $shadowRows | Sort-Object Inherited, OverGroup |
        Select-Object ShadowAdmin, ControlRight, OverGroup, Inherited -First 25 |
        Format-Table -AutoSize
}
