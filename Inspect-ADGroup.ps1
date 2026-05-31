<#
.SYNOPSIS
    Deep-dive a SINGLE AD group: what it is, what it's nested into, what's inside it,
    who can take it over, and (the hard part) what dangerous rights it HOLDS across AD.

.DESCRIPTION
    Use this to investigate a group that the main audit did NOT flag, and decide whether
    it is actually sensitive. Answers four questions:
      1. WHAT IS IT      - SID, adminCount, scope, description, member count.
      2. NESTED INTO     - which groups it is a member of (recursive); flags privileged parents.
      3. INSIDE IT       - direct + recursive members.
      4. CONTROL OVER IT - who has GenericAll/WriteDacl/WriteOwner/write-member on the group object.
      5. RIGHTS IT HOLDS - where this group (or a group it is nested into) was granted a
                           dangerous right: Reset Password, GenericAll, DCSync, etc.

    "Rights it holds" scans ACLs. Delegations almost always live on OUs/containers and
    inherit down, so by default it scans only: the domain root (DCSync), all OUs, all
    containers (incl. AdminSDHolder), all GPOs, and adminCount=1 group objects (Tier0
    takeover). That is a small, fast set. Use -FullDomainRights for an exhaustive scan
    (every object - slow), or -RightsScanBase to point at one subtree.

.PARAMETER Group
    Group to inspect: Name, sAMAccountName, or DistinguishedName.

.PARAMETER FullDomainRights
    Scan EVERY object's ACL for rights this group holds (slow on big domains).

.PARAMETER RightsScanBase
    Limit the "rights it holds" scan to this DN subtree (e.g. an OU).

.EXAMPLE
    .\Inspect-ADGroup.ps1 -Group "Helpdesk-Tier2"

.EXAMPLE
    .\Inspect-ADGroup.ps1 -Group "Some-Delegated-Group" -RightsScanBase "OU=Branches,DC=corp,DC=local"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$Group,
    [string]$ConfigPath = "$PSScriptRoot\config.json",
    [string]$RightsScanBase,
    [switch]$FullDomainRights
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module ActiveDirectory -ErrorAction Stop

# Optional config (for the sensitiveRights list); falls back to sane defaults.
$cfg = $null
if (Test-Path $ConfigPath) { try { $cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json } catch {} }

function H1($m){ Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Info($k,$v){ Write-Host ("  {0,-16}: {1}" -f $k, $v) }
function Hot($m){ Write-Host "  [!] $m" -ForegroundColor Red }
function Ok2($m){ Write-Host "  $m" -ForegroundColor Gray }

$domain   = Get-ADDomain
$server   = ($domain.PDCEmulator)
$adP      = @{ Server = $server }
$domainDN = $domain.DistinguishedName
$domSid   = $domain.DomainSID.Value

# Well-known extended-right / attribute GUIDs we always care about
$GUID = @{
    ResetPassword = '00299570-246d-11d0-a768-00aa006e0529'
    ChangePassword= 'ab721a53-1e2f-11d0-9819-00aa0040529b'
    Member        = 'bf9679c0-0de6-11d0-a285-00aa003049e2'
    DCSync1       = '1131f6aa-9c07-11d1-f79f-00c04fc2dcd2'  # Replicating Directory Changes
    DCSync2       = '1131f6ad-9c07-11d1-f79f-00c04fc2dcd2'  # Replicating Directory Changes All
}
$dangerFlags = 'GenericAll|WriteDacl|WriteOwner|GenericWrite'

# ----------------------------------------------------------------------------
# Resolve the group
# ----------------------------------------------------------------------------
$g = $null
try { $g = Get-ADGroup -Identity $Group -Properties adminCount,description,memberOf,member,groupScope,groupCategory,whenCreated,objectSid @adP } catch {}
if (-not $g) {
    $g = Get-ADGroup -LDAPFilter "(|(name=$Group)(sAMAccountName=$Group))" -Properties adminCount,description,memberOf,member,groupScope,groupCategory,whenCreated,objectSid @adP | Select-Object -First 1
}
if (-not $g) { throw "Group not found: '$Group'" }

$verdict = New-Object System.Collections.Generic.List[string]

H1 "1. WHAT IS IT"
Info 'Name'        $g.Name
Info 'sAMAccount'  $g.sAMAccountName
Info 'DN'          $g.DistinguishedName
Info 'SID'         $g.objectSid.Value
Info 'Scope/Type'  "$($g.groupScope) / $($g.groupCategory)"
Info 'Created'     $g.whenCreated
Info 'Description' $g.description
$directMembers = @($g.member)
Info 'DirectMembers' $directMembers.Count
if ($g.adminCount -eq 1) { Hot "adminCount=1  -> AdminSDHolder-protected (privileged)"; $verdict.Add('adminCount=1 (protected)') }

# ----------------------------------------------------------------------------
# 2. Nested into (recursive parents)
# ----------------------------------------------------------------------------
H1 "2. NESTED INTO (groups it is a member of, recursive)"
$parents = @(Get-ADGroup -LDAPFilter "(member:1.2.840.113556.1.4.1941:=$($g.DistinguishedName))" -Properties adminCount @adP)
if (-not $parents.Count) { Ok2 "(not nested into any group)" }
foreach ($p in $parents) {
    $priv = ($p.adminCount -eq 1)
    if ($priv) { Hot "$($p.Name)   (privileged / adminCount=1)"; $verdict.Add("nested into privileged: $($p.Name)") }
    else       { Ok2 "$($p.Name)" }
}

# ----------------------------------------------------------------------------
# 3. Inside it (members, recursive)
# ----------------------------------------------------------------------------
H1 "3. INSIDE IT (effective members, recursive)"
$members = @(Get-ADObject -LDAPFilter "(memberOf:1.2.840.113556.1.4.1941:=$($g.DistinguishedName))" -Properties objectClass,sAMAccountName @adP)
$byClass = $members | Group-Object objectClass | Sort-Object Count -Descending
if (-not $members.Count) { Ok2 "(empty)" }
foreach ($c in $byClass) { Info $c.Name $c.Count }

# ----------------------------------------------------------------------------
# 4. Control OVER the group object (who can take it over)
# ----------------------------------------------------------------------------
H1 "4. CONTROL OVER THIS GROUP (who can take it over)"
$sd = (Get-ADObject -Identity $g.DistinguishedName -Properties nTSecurityDescriptor @adP).nTSecurityDescriptor
$ctrlSeen = $false
foreach ($ace in $sd.Access) {
    if ($ace.AccessControlType -ne 'Allow') { continue }
    $r = $ace.ActiveDirectoryRights.ToString(); $ot = "$($ace.ObjectType)".ToLower()
    $hit = $null
    if ($r -match $dangerFlags) { $hit = ($r -split ',')[0].Trim() }
    elseif ($r -match 'WriteProperty' -and ($ot -eq $GUID.Member)) { $hit = 'Write membership' }
    if ($hit) {
        $ctrlSeen = $true
        $tag = if ($ace.IsInherited) { '(inherited)' } else { '(DIRECT)' }
        Write-Host ("  {0,-22} {1,-18} {2}" -f $ace.IdentityReference, $hit, $tag) -ForegroundColor $(if ($ace.IsInherited) { 'Gray' } else { 'Yellow' })
    }
}
if (-not $ctrlSeen) { Ok2 "(no dangerous control ACEs found)" }

# ----------------------------------------------------------------------------
# 5. Rights it HOLDS (effective: this group + groups it is nested into)
# ----------------------------------------------------------------------------
H1 "5. RIGHTS THIS GROUP HOLDS (what it can DO across AD)"

# Effective trustee SIDs = the group itself + every group it is nested into.
$trustee = @{}
$trustee[$g.objectSid.Value] = $g.Name
foreach ($p in $parents) { try { $trustee[$p.objectSid.Value] = $p.Name } catch {} }

# Build the target object set whose ACLs we read.
Write-Host "  Collecting target ACLs..." -ForegroundColor DarkGray
$targets = New-Object System.Collections.Generic.List[object]
if ($FullDomainRights) {
    $base = if ($RightsScanBase) { $RightsScanBase } else { $domainDN }
    $targets.AddRange(@(Get-ADObject -LDAPFilter '(|(objectClass=user)(objectClass=group)(objectClass=computer)(objectClass=organizationalUnit)(objectClass=container))' -SearchBase $base -Properties nTSecurityDescriptor @adP))
}
elseif ($RightsScanBase) {
    $targets.AddRange(@(Get-ADObject -LDAPFilter '(objectClass=*)' -SearchBase $RightsScanBase -Properties nTSecurityDescriptor @adP))
}
else {
    # Fast default: domain root (DCSync) + OUs + containers (AdminSDHolder) + GPOs + adminCount=1 groups.
    $targets.Add((Get-ADObject -Identity $domainDN -Properties nTSecurityDescriptor @adP))
    $targets.AddRange(@(Get-ADOrganizationalUnit -Filter * -Properties nTSecurityDescriptor @adP))
    $targets.AddRange(@(Get-ADObject -LDAPFilter '(objectClass=container)' -SearchBase $domainDN -Properties nTSecurityDescriptor @adP))
    $targets.AddRange(@(Get-ADObject -LDAPFilter '(objectClass=groupPolicyContainer)' -SearchBase $domainDN -Properties nTSecurityDescriptor @adP))
    $targets.AddRange(@(Get-ADObject -LDAPFilter '(&(objectClass=group)(adminCount=1))' -SearchBase $domainDN -Properties nTSecurityDescriptor @adP))
}
Write-Host ("  Scanning {0} target object(s)..." -f $targets.Count) -ForegroundColor DarkGray

$held = New-Object System.Collections.Generic.List[object]
foreach ($obj in $targets) {
    $osd = $obj.nTSecurityDescriptor
    if (-not $osd) { continue }
    foreach ($ace in $osd.Access) {
        if ($ace.AccessControlType -ne 'Allow') { continue }
        $sid = $null
        try {
            $sid = if ($ace.IdentityReference -is [System.Security.Principal.SecurityIdentifier]) { $ace.IdentityReference.Value }
                   else { ([System.Security.Principal.NTAccount]$ace.IdentityReference).Translate([System.Security.Principal.SecurityIdentifier]).Value }
        } catch { continue }
        if (-not $trustee.ContainsKey($sid)) { continue }

        $r = $ace.ActiveDirectoryRights.ToString(); $ot = "$($ace.ObjectType)".ToLower()
        $what = $null
        if     ($ot -eq $GUID.ResetPassword) { $what = 'Reset Password' }
        elseif ($ot -eq $GUID.DCSync1)       { $what = 'DCSync (Repl Changes)' }
        elseif ($ot -eq $GUID.DCSync2)       { $what = 'DCSync (Repl Changes ALL)' }
        elseif ($ot -eq $GUID.Member -and $r -match 'WriteProperty|Self') { $what = 'Write membership' }
        if (-not $what -and $r -match $dangerFlags) { $what = ($r -split ',')[0].Trim() }
        if ($what) {
            $held.Add([pscustomobject]@{
                Can        = $what
                OnTarget   = $obj.Name
                TargetClass= $obj.objectClass
                ViaTrustee = $trustee[$sid]
                Inherited  = $ace.IsInherited
                TargetDN   = $obj.DistinguishedName
            })
        }
    }
}

if (-not $held.Count) {
    Ok2 "(no dangerous rights held in the scanned scope)"
    if (-not $FullDomainRights -and -not $RightsScanBase) {
        Write-Host "  (default scan = domain root + OUs + containers + GPOs + Tier0 groups. Use -FullDomainRights for every object.)" -ForegroundColor DarkGray
    }
} else {
    $held | Sort-Object Inherited, Can | ForEach-Object {
        $tag = if ($_.Inherited) { 'inherited' } else { 'DIRECT' }
        Write-Host ("  CAN {0,-26} ON {1,-30} [{2}] via {3}" -f $_.Can, $_.OnTarget, $tag, $_.ViaTrustee) -ForegroundColor Yellow
        $verdict.Add("holds '$($_.Can)' on $($_.OnTarget)")
    }
}

# ----------------------------------------------------------------------------
# Verdict
# ----------------------------------------------------------------------------
H1 "VERDICT"
if ($verdict.Count) {
    Hot "This group looks SENSITIVE. Reasons:"
    $verdict | Select-Object -Unique | ForEach-Object { Write-Host "    - $_" -ForegroundColor Red }
    Write-Host "  -> Consider adding it to config.json (groupNamePatterns) or rely on adminCount/nesting/rights discovery." -ForegroundColor White
} else {
    Write-Host "  No privileged signals found in the scanned scope." -ForegroundColor Green
    Write-Host "  If you still believe it is sensitive, try -FullDomainRights or -RightsScanBase <OU>." -ForegroundColor Gray
}
