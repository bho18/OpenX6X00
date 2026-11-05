import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.*;
import ghidra.program.model.mem.*;
import java.math.BigInteger;
public class PttSetup extends GhidraScript {
 public void run() throws Exception {
  Memory m=currentProgram.getMemory();
  for(MemoryBlock b:m.getBlocks()){b.setRead(true);b.setExecute(true);b.setWrite(false);}
  currentProgram.getProgramContext().setValue(currentProgram.getRegister("TMode"),toAddr(0x08020000L),toAddr(0x0809ffffL),BigInteger.ONE);
  long[][] regions={{0x20000000L,0x20000L},{0x24000000L,0x80000L},{0x30000000L,0x80000L},{0x38000000L,0x10000L},{0x40000000L,0x20000000L},{0xe0000000L,0x100000L}};
  for(int i=0;i<regions.length;i++){MemoryBlock b=m.createUninitializedBlock("ram_mmio_"+i,toAddr(regions[i][0]),regions[i][1],false);b.setRead(true);b.setWrite(true);b.setExecute(false);}
  for(int i=1;i<168;i++){long v=getInt(toAddr(0x08020000L+i*4))&0xffffffffL;if((v&1)!=0 && v>=0x08020000L && v<0x08050000L){Address a=toAddr(v&~1L);addEntryPoint(a);disassemble(a);if(getFunctionAt(a)==null)createFunction(a,"vector_"+i);}}
  long[] seeds={0x08023cc4L,0x0802369cL,0x08024880L,0x0803dd20L,0x0803d9a0L,0x0803786cL,0x0802a06cL,0x0802a080L,0x080450c8L};
  for(long v:seeds){Address a=toAddr(v);addEntryPoint(a);disassemble(a);if(getFunctionAt(a)==null)createFunction(a,"ptt_seed_"+Long.toHexString(v));}
 }
}
