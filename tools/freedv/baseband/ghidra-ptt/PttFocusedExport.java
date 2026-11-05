import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.address.*;
import java.io.*;
public class PttFocusedExport extends GhidraScript {
 public void run() throws Exception {
  File dir=new File(getScriptArgs()[0]);dir.mkdirs();DecompInterface d=new DecompInterface();d.openProgram(currentProgram);
  java.util.TreeSet<Long> targets=new java.util.TreeSet<>();
  long[] fixed={0x08022a18L,0x08044b54L,0x08044e34L,0x08044f34L,0x0803ec30L,0x0803ec70L,0x0803ebccL,0x0803ebe4L,0x0803ecc8L,0x08024468L,0x080244f0L,0x080243e0L,0x0803dd20L,0x0803786cL,0x0803d9a0L};for(long a:fixed)targets.add(a);
  for(int i=0;i<14;i++)for(int j=1;j<=2;j++){long a=getInt(toAddr(0x0806b19cL+i*12+j*4))&0xffffffffL;if(a!=0)targets.add(a&~1L);}
  for(long a:targets){Function f=getFunctionAt(toAddr(a));if(f==null){println("MISSING "+Long.toHexString(a));continue;}DecompileResults r=d.decompileFunction(f,60,monitor);try(PrintWriter out=new PrintWriter(new File(dir,Long.toHexString(a)+".c"))){if(r.decompileCompleted())out.print(r.getDecompiledFunction().getC());else out.println("DECOMPILE_ERROR "+r.getErrorMessage());}}
  d.dispose();
 }
}
