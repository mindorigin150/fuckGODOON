[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new()
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Text.RegularExpressions;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class MiniSessionReader {
 [StructLayout(LayoutKind.Sequential)] struct MBI { public IntPtr BaseAddress,AllocationBase; public uint AllocationProtect; public UIntPtr RegionSize; public uint State,Protect,Type; }
 [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,int pid);
 [DllImport("kernel32.dll")] static extern UIntPtr VirtualQueryEx(IntPtr process,IntPtr address,out MBI info,UIntPtr size);
 [DllImport("kernel32.dll")] static extern bool ReadProcessMemory(IntPtr process,IntPtr address,byte[] data,UIntPtr size,out UIntPtr read);
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
 static Regex jwt=new Regex(@"Bearer eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])",RegexOptions.Compiled);
 public static string[] Read(int pid, bool capture) {
  IntPtr process=OpenProcess(0x410,false,pid);
  if(process==IntPtr.Zero) return new string[]{};
  var found=new HashSet<string>(); int hostHits=0;
  try {
   long address=0;
   while(address<0x00007fffffff0000L) {
    MBI info;
    if(VirtualQueryEx(process,new IntPtr(address),out info,(UIntPtr)Marshal.SizeOf(typeof(MBI)))==UIntPtr.Zero) break;
    long size=(long)info.RegionSize.ToUInt64();
    if(size<=0 || info.BaseAddress.ToInt64()+size<=address) break;
    if(info.State==0x1000 && info.Type==0x20000 && (info.Protect&0x101)==0) {
     for(long offset=0;offset<size;offset+=1024*1024-4096) {
      int length=(int)Math.Min(1024*1024,size-offset);
      byte[] bytes=new byte[length]; UIntPtr read;
      ReadProcessMemory(process,new IntPtr(info.BaseAddress.ToInt64()+offset),bytes,(UIntPtr)length,out read);
      int n=(int)read.ToUInt64();
      if(n<=0)continue; string ascii=Encoding.ASCII.GetString(bytes,0,n); if(ascii.Contains("mini-club.codoon.com"))hostHits++;
      if(capture) foreach(Match m in jwt.Matches(ascii))found.Add(m.Value);
      if(capture) foreach(Match m in jwt.Matches(Encoding.Unicode.GetString(bytes,0,n-(n%2))))found.Add(m.Value);
     }
    }
    address=info.BaseAddress.ToInt64()+size;
   }
  } finally { CloseHandle(process); }
  if(!capture) return hostHits > 0 ? new string[]{"target"} : new string[]{}; var result=new string[found.Count];found.CopyTo(result);return result;
 }
}
'@
$ErrorActionPreference = "Stop"
$currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$foundTokens = [System.Collections.Generic.HashSet[string]]::new()
$renderers = Get-CimInstance Win32_Process -Filter "name = 'WeChatAppEx.exe'" | Where-Object { $_.CommandLine -match '--type=renderer' }
foreach ($renderer in $renderers) {
 try { $owner = Invoke-CimMethod -InputObject $renderer -MethodName GetOwnerSid -ErrorAction Stop } catch { continue }
 if($owner.ReturnValue -ne 0 -or $owner.Sid -ne $currentSid) { continue }
 if ([MiniSessionReader]::Read($renderer.ProcessId,$false).Length -gt 0) {
  foreach($value in [MiniSessionReader]::Read($renderer.ProcessId,$true)) { $foundTokens.Add($value) | Out-Null }
 }
}
@($foundTokens) | ConvertTo-Json -Compress
