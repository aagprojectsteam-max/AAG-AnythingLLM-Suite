"use strict";
const http = require('http');
const {AagError} = require('./errors');
function status() {
  return new Promise((resolve,reject) => {
    const req=http.get({socketPath:'/app/server/storage/aag-image-lifecycle/control.sock',path:'/status',timeout:10000},res=>{
      let data='';res.on('data',part=>{data+=part;if(data.length>65536)req.destroy(new Error('oversize'));});
      res.on('error',reject);res.on('end',()=>{try{if(res.statusCode!==200)throw Error();resolve(JSON.parse(data));}catch{reject(Error('invalid status'));}});
    });req.on('error',reject);req.on('timeout',()=>req.destroy(Error('timeout')));
  });
}
async function requireReady(probe=status) {
  let current;
  try {current=await probe();} catch {throw new AagError('LIFECYCLE_UNAVAILABLE','Image system status is unavailable. Retry from the Composer lifecycle control.',true);}
  if(current?.schema!=='aag.image.lifecycle.v1'||current.state!=='READY') {
    const state=['OFF','STARTING','STOPPING','ERROR'].includes(current?.state)?current.state:'UNKNOWN';
    throw new AagError('LIFECYCLE_'+state,`AAG Image System is ${state}. Wait for READY in Composer before generating.`,true);
  }
}
module.exports={status,requireReady};
