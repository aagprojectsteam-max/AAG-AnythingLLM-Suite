"use strict";
// Browser auth/workspace/origin middleware belongs to the native Composer router.
// Mutations additionally validate the existing private Composer CSRF session.
const http = require('http');
const SOCKET = '/app/server/storage/aag-image-lifecycle/control.sock';
function exchange(socketPath, path, method, headers = {}, body = null) {
  return new Promise((resolve, reject) => {
    const request = http.request({socketPath, path, method, headers, timeout: 12_000}, response => {
      let raw = ''; response.on('data', chunk => { raw += chunk; if (raw.length > 65536) request.destroy(new Error('oversize')); });
      response.on('end', () => { try { resolve({status: response.statusCode, data: JSON.parse(raw)}); } catch { reject(new Error('invalid status')); } });
      response.on('error', reject);
    });
    request.on('error', reject); request.on('timeout', () => request.destroy(new Error('timeout'))); request.end(body);
  });
}
function lifecycleProxy({composerSocket, sessionCookie, csrf, transport = exchange}) {
  return async function route(request, response, action) {
    response.setHeader('Cache-Control', 'no-store');
    if (!['status','start','stop'].includes(action) || request.method !== (action === 'status' ? 'GET' : 'POST')) return response.status(404).json({error:{code:'INVALID_ACTION'}});
    if (Object.keys(request.query || {}).length || (action !== 'status' && (request.body === null || Array.isArray(request.body) || typeof request.body !== 'object' || Object.keys(request.body).length))) return response.status(400).json({error:{code:'ARGUMENTS_FORBIDDEN',message:'Lifecycle actions accept no arguments.'}});
    try {
      if (action !== 'status') {
        const cookie=sessionCookie(request.headers.cookie), token=csrf(request.header('X-AAG-CSRF'));
        if (!cookie || !token) return response.status(403).json({error:{code:'COMPOSER_CSRF_INVALID',message:'Refresh the Composer session and retry.'}});
        const auth=await transport(composerSocket, '/composer/authorize', 'POST', {Host:'127.0.0.1:18080',Origin:'http://127.0.0.1:18080',Referer:'http://127.0.0.1:18080/composer/',Cookie:cookie,'X-AAG-CSRF':token,'Content-Type':'application/json','Content-Length':'2'}, '{}');
        if (auth.status !== 200 || auth.data.authorized !== true) return response.status(403).json({error:{code:'COMPOSER_CSRF_INVALID',message:'Refresh the Composer session and retry.'}});
      }
      const result=await transport(SOCKET, '/'+action, request.method, action==='status'?{}:{'Content-Type':'application/json','Content-Length':'2'}, action==='status'?null:'{}');
      return response.status(result.status).json(result.data);
    } catch { return response.status(503).json({error:{code:'LIFECYCLE_UNAVAILABLE',component:'control',message:'Image lifecycle control is unavailable. Retry shortly.',retryable:true}}); }
  };
}
module.exports={lifecycleProxy, exchange};
