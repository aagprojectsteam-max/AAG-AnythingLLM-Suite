"use strict";
// Header facts supplement the engine's full output decoder and persisted hash.
// No format conversion occurs in artifact storage or download.
function inspect(bytes) {
  let format,width,height;
  const bad=()=>{throw new Error("AAG_ARTIFACT_FORMAT_INVALID");};
  if (bytes.length>=33 && bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])) && bytes.toString("ascii",12,16)==="IHDR") {
    format="png";width=bytes.readUInt32BE(16);height=bytes.readUInt32BE(20);
  } else if (bytes.length>=4 && bytes[0]===255 && bytes[1]===216) {
    format="jpeg";let pos=2;
    while(pos+4<=bytes.length) {
      if(bytes[pos++]!==255)bad();while(bytes[pos]===255)pos++;
      const marker=bytes[pos++];if(marker===0xda||marker===0xd9)break;
      if(marker===0x01||marker>=0xd0&&marker<=0xd7)continue;
      if(pos+2>bytes.length)bad();const size=bytes.readUInt16BE(pos);
      if(size<2||pos+size>bytes.length)bad();
      if([0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf].includes(marker)) {
        if(size<8)bad();height=bytes.readUInt16BE(pos+3);width=bytes.readUInt16BE(pos+5);break;
      }
      pos+=size;
    }
  } else if(bytes.length>=30 && bytes.toString("ascii",0,4)==="RIFF" && bytes.toString("ascii",8,12)==="WEBP") {
    if(bytes.readUInt32LE(4)+8!==bytes.length)bad();format="webp";
    const kind=bytes.toString("ascii",12,16),size=bytes.readUInt32LE(16);if(size+20>bytes.length)bad();
    if(kind==="VP8X"&&size>=10) {if(bytes[20]&2)bad();width=1+bytes.readUIntLE(24,3);height=1+bytes.readUIntLE(27,3);}
    else if(kind==="VP8 "&&size>=10&&bytes.subarray(23,26).equals(Buffer.from([0x9d,1,0x2a]))) {width=bytes.readUInt16LE(26)&0x3fff;height=bytes.readUInt16LE(28)&0x3fff;}
    else if(kind==="VP8L"&&size>=5&&bytes[20]===0x2f) {const bits=bytes.readUInt32LE(21);width=1+(bits&0x3fff);height=1+((bits>>>14)&0x3fff);}
    else bad();
  } else bad();
  if(!width||!height||width>16384||height>16384||width*height>200000000)bad();
  return {format,mime:`image/${format}`,width,height};
}
function matchesFilename(filename,format) {
  const ext=String(filename).split(".").at(-1);
  return ({png:"png",jpg:"jpeg",jpeg:"jpeg",webp:"webp"})[ext]===format;
}
module.exports={inspect,matchesFilename};
