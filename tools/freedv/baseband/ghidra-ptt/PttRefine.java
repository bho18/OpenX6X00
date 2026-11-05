import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.listing.*;
import java.io.*;
public class PttRefine extends GhidraScript {
 public void run() throws Exception {
  Memory m=currentProgram.getMemory();MemoryBlock b=m.getBlock(toAddr(0x24000000L));
  if(!b.isInitialized()) {m.split(b,toAddr(0x240056d8L));byte[] init=getBytes(toAddr(0x0806ea50L),0x56d8);m.convertToInitialized(b,(byte)0);m.setBytes(toAddr(0x24000000L),init);}
  for(int i=0;i<14;i++)for(int j=1;j<=2;j++){long ptr=getInt(toAddr(0x0806b19cL+i*12+j*4))&0xffffffffL;if(ptr==0)continue;Address a=toAddr(ptr&~1L);addEntryPoint(a);disassemble(a);if(getFunctionAt(a)==null && getFunctionContaining(a)==null)createFunction(a,"console_"+i+"_"+j);}
 }
}
