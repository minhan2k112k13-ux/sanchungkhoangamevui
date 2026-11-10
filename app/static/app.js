(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = { csrf: '', user: null, market: null, selected: 'BTC', mode: 'buy', room: 'total', shop: null, poll: null };
  const money = (n) => '$' + Number(n || 0).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
  const number = (n, d=6) => Number(n || 0).toLocaleString('en-US', {minimumFractionDigits:0, maximumFractionDigits:d});
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmtDate = (s) => { if (!s) return '—'; const d = new Date(s); return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('vi-VN'); };
  const toast = (msg, error=false) => { const el=document.createElement('div'); el.className='toast'+(error?' error':''); el.textContent=msg; $('toast-area').append(el); setTimeout(()=>el.remove(),4200); };
  const setMessage = (id,msg,error=false) => { const el=$(id); if(el){el.textContent=msg||'';el.style.color=error?'#ff9ca4':'';} };
  async function api(path, options={}) {
    const opts={...options, headers:{...(options.headers||{})}};
    if (opts.body && typeof opts.body !== 'string') { opts.headers['Content-Type']='application/json'; opts.body=JSON.stringify(opts.body); }
    if (opts.method && !['GET','HEAD'].includes(opts.method.toUpperCase())) opts.headers['X-CSRF-Token']=state.csrf;
    let response;
    try { response=await fetch(path,opts); }
    catch(e){ throw new Error('Không kết nối được máy chủ. Kiểm tra Render/Neon và thử tải lại.'); }
    const type=response.headers.get('content-type')||'';
    const data=type.includes('application/json')?await response.json():await response.text();
    if(!response.ok) throw new Error(typeof data==='object'?(data.detail||data.message||'Yêu cầu thất bại.'):String(data||'Yêu cầu thất bại.'));
    return data;
  }
  async function refreshCsrf(){ const s=await api('/api/auth/status'); state.csrf=s.csrf; return s; }
  function authTab(which){
    document.querySelectorAll('[data-auth-tab]').forEach(b=>b.classList.toggle('active',b.dataset.authTab===which));
    ['register','login'].forEach(k=>$(`${k}-form`).classList.toggle('hidden',k!==which));
    $('auth-title').textContent=({register:'Đăng ký tài khoản',login:'Đăng nhập'})[which]||'Đăng nhập';
    $('auth-error').textContent='';
  }
  function showAuth(which='login'){ $('app-screen').classList.add('hidden');$('auth-screen').classList.remove('hidden');authTab(which); }
  function showApp(){ $('auth-screen').classList.add('hidden');$('app-screen').classList.remove('hidden'); }
  function isStaff(u=state.user){ return !!u&&(u.role==='owner'||['admin','superadmin','coordinate'].includes(u.role)); }
  function isOwner(u=state.user){ return !!u&&(u.role==='owner'||u.role_label==='OWNER'); }
  function showView(name){
    document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id===`view-${name}`));
    document.querySelectorAll('[data-view]').forEach(v=>v.classList.toggle('active',v.dataset.view===name));
    const names={market:'Tổng quan thị trường',portfolio:'Danh mục tài sản',membership:'Gói thành viên',community:'Cộng đồng',profile:'Hồ sơ & ví',admin:'Quản trị hệ thống'};
    $('page-title').textContent=names[name]||'SÀN CHỨNG KHOÁN';
    if(name==='portfolio') loadPortfolio(); if(name==='membership') loadMembership(); if(name==='community') loadChat(); if(name==='admin') loadAdmin(); if(name==='profile') loadInbox();
  }
  function renderUser(u){
    state.user=u; showApp();
    $('user-name').textContent=u.display_name||u.username; $('user-role').textContent=u.role_label;
    $('user-avatar').textContent=(u.display_name||u.username||'U').slice(0,1).toUpperCase();
    $('profile-avatar').textContent=$('user-avatar').textContent; $('profile-name-title').textContent=u.display_name||u.username;
    $('profile-role-pill').textContent=u.role_label; $('profile-id').textContent=u.account_id; $('profile-username').textContent=u.username;
    $('profile-created').textContent=fmtDate(u.created_at); $('profile-display').value=u.display_name||u.username;
    $('profile-bio').value=(u.profile||{}).bio||'';
    $('request-admin-btn').classList.toggle('hidden',u.role!=='user'||u.role_request_status==='pending'||u.role_request_status==='approved');
    $('cancel-membership-btn').classList.toggle('hidden',!u.membership||['ruby','emerald'].includes(u.membership));
    $('stat-balance').textContent=money(u.balance); $('pf-balance').textContent=money(u.balance);
    $('inbox-count').textContent=u.inbox_unread||0; $('inbox-count').classList.toggle('hidden',!(u.inbox_unread>0));
    document.querySelectorAll('.staff-only').forEach(x=>x.classList.toggle('hidden',!isStaff(u)));
    document.querySelectorAll('.owner-only').forEach(x=>x.classList.toggle('hidden',!isOwner(u)));
    document.querySelectorAll('[data-room="qtv"]').forEach(x=>x.classList.toggle('hidden',!isStaff(u)));
    $('stat-tier').textContent=(u.membership_label||'THÀNH VIÊN THƯỜNG').split(' · ')[0];
    $('stat-tier-foot').textContent=u.forecast_seconds?`Dự báo mô phỏng: ${u.forecast_seconds}s`:'Khám phá quyền lợi thành viên';
    if(u.role==='user' && u.role_request_status==='pending') setMessage('profile-message','Yêu cầu ADMIN đang chờ OWNER xét duyệt.');
    if(!isOwner(u)) document.querySelectorAll('.owner-only').forEach(x=>x.classList.add('hidden'));
  }
  async function hydrate(){
    const me=await api('/api/me'); renderUser(me.user); state.user.profile=me.profile; $('profile-bio').value=(me.profile||{}).bio||'';
    await Promise.all([loadMarket(),loadMembership(),loadPortfolio(),loadInbox()]);
    if(state.poll) clearInterval(state.poll);
    state.poll=setInterval(()=>{ if(state.user){loadMarket().catch(()=>{}); refreshMe().catch(()=>{});} },7000);
  }
  async function refreshMe(){ const r=await api('/api/me'); renderUser(r.user); state.user.profile=r.profile; $('profile-bio').value=(r.profile||{}).bio||''; }
  async function handleAuth(formId,path,successText){
    const form=$(formId); const payload=Object.fromEntries(new FormData(form).entries());
    $('auth-error').textContent=''; const button=form.querySelector('button[type="submit"]'); if(button)button.disabled=true;
    try { const result=await api(path,{method:'POST',body:payload}); if(result.csrf)state.csrf=result.csrf; if(result.user){if(result.csrf)state.csrf=result.csrf;else await refreshCsrf();renderUser(result.user);toast(result.message||successText);await hydrate();} else {toast(result.message||successText);}; }
    catch(e){$('auth-error').textContent=e.message;}
    finally{if(button)button.disabled=false;}
  }
  function currentAsset(){ return state.market?.assets?.find(a=>a.symbol===state.selected)||null; }
  function renderMarket(data){
    state.market=data;
    const filter=$('category-filter').value; const assets=data.assets.filter(a=>filter==='all'||a.category===filter);
    $('asset-list').innerHTML=assets.map(a=>`<button class="asset-row ${a.symbol===state.selected?'selected':''}" data-symbol="${esc(a.symbol)}"><span class="asset-name"><b>${esc(a.symbol)} · ${esc(a.name)}</b><small>${esc(a.category)} ${a.open?'':'· TẠM NGHỈ'}</small></span><span class="asset-price"><b>${money(a.price)}</b><small class="${a.change_pct>=0?'up':'down'}">${a.change_pct>=0?'+':''}${Number(a.change_pct).toFixed(3)}%</small></span></button>`).join('');
    $('market-status').textContent=data.status||'Giá thị trường mô phỏng'; $('chart-updated').textContent='Cập nhật: '+fmtDate(data.updated_at);
    $('market-table').innerHTML=data.assets.slice().sort((a,b)=>b.change_pct-a.change_pct).map(a=>`<div class="market-table-row"><b>${esc(a.symbol)}</b><span>${money(a.price)}</span><strong class="${a.change_pct>=0?'up':'down'}">${a.change_pct>=0?'+':''}${a.change_pct.toFixed(3)}%</strong></div>`).join('');
    const asset=currentAsset()||data.assets[0]; if(asset)renderSelected(asset);
  }
  function renderSelected(a){
    state.selected=a.symbol;
    $('chart-category').textContent=a.category.toUpperCase(); $('chart-name').innerHTML=`${esc(a.name)} <span id="chart-symbol">${esc(a.symbol)}/USD</span>`;
    $('chart-price').textContent=money(a.price); $('chart-change').textContent=(a.change_pct>=0?'+':'')+a.change_pct.toFixed(3)+'%'; $('chart-change').className=a.change_pct>=0?'change up':'change down';
    $('trade-symbol').textContent=a.symbol; $('trade-current-price').textContent=money(a.price);
    document.querySelectorAll('.asset-row').forEach(x=>x.classList.toggle('selected',x.dataset.symbol===a.symbol));
    updateTradePreview(); drawChart(a);
  }
  function drawChart(a){
    const canvas=$('price-chart'); if(!canvas)return; const rect=canvas.getBoundingClientRect(); if(!rect.width||!rect.height)return;
    const dpr=Math.min(window.devicePixelRatio||1,2); canvas.width=Math.floor(rect.width*dpr);canvas.height=Math.floor(rect.height*dpr);
    const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);const w=rect.width,h=rect.height;ctx.clearRect(0,0,w,h);
    const pad={l:8,r:56,t:14,b:22},cw=w-pad.l-pad.r,ch=h-pad.t-pad.b;
    for(let i=0;i<5;i++){const y=pad.t+ch*i/4;ctx.strokeStyle='rgba(140,165,200,.12)';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke();}
    const vals=(a.history||[]).slice(-200);if(vals.length<2)return;const min=Math.min(...vals),max=Math.max(...vals);const span=Math.max(max-min,Math.abs(max)*.00001,1e-8);
    const x=i=>pad.l+cw*i/(vals.length-1), y=v=>pad.t+ch*(1-(v-min)/span);
    const grad=ctx.createLinearGradient(0,pad.t,0,h-pad.b);grad.addColorStop(0,'rgba(85,217,229,.22)');grad.addColorStop(1,'rgba(85,217,229,0)');
    ctx.beginPath();vals.forEach((v,i)=>i?ctx.lineTo(x(i),y(v)):ctx.moveTo(x(i),y(v)));ctx.lineTo(x(vals.length-1),h-pad.b);ctx.lineTo(x(0),h-pad.b);ctx.closePath();ctx.fillStyle=grad;ctx.fill();
    const maColors={MA5:'#f7c948',MA10:'#ff69b4',MA20:'#00d8e9',MA50:'#9c88ff',MA200:'#fff'};
    for(const [key,color] of Object.entries(maColors)){const n=Number(key.slice(2));if(vals.length<n)continue;ctx.beginPath();let started=false;for(let i=n-1;i<vals.length;i++){let avg=0;for(let j=i-n+1;j<=i;j++)avg+=vals[j];avg/=n;const xx=x(i),yy=y(avg);if(!started){ctx.moveTo(xx,yy);started=true}else ctx.lineTo(xx,yy);}ctx.strokeStyle=color;ctx.globalAlpha=.7;ctx.lineWidth=key==='MA5'?1.35:1;ctx.stroke();ctx.globalAlpha=1;}
    ctx.beginPath();vals.forEach((v,i)=>i?ctx.lineTo(x(i),y(v)):ctx.moveTo(x(i),y(v)));ctx.strokeStyle='#67d9e8';ctx.lineWidth=2;ctx.stroke();
    ctx.fillStyle='#a8bdd7';ctx.font='10px DM Mono,monospace';ctx.textAlign='right';for(let i=0;i<5;i++){const v=max-span*i/4;ctx.fillText(v.toLocaleString('en-US',{maximumFractionDigits:v<5?4:2}),w-5,pad.t+ch*i/4+3);}
    ctx.fillStyle='#6f829f';ctx.textAlign='left';ctx.fillText('200 MẪU GẦN NHẤT',pad.l,h-5);
  }
  async function loadMarket(){const m=await api('/api/market');renderMarket(m);}
  function getNumericInput(v){let s=String(v||'').trim().replace(/\s/g,'');if(s.includes(',')&&s.includes('.'))s=s.replace(/\./g,'').replace(',','.');else if(s.includes(','))s=s.replace(',','.');else if((s.match(/\./g)||[]).length>1)s=s.replace(/\./g,'');const n=Number(s);return Number.isFinite(n)&&n>=0?n:0;}
  function updateTradePreview(){
    const a=currentAsset();if(!a||!state.user)return;const raw=getNumericInput($('trade-amount').value);const pct=Number($('trade-percent').value)||0;
    $('trade-percent-label').textContent=pct+'%';const qty=$('trade-qty');const after=$('trade-after');
    let available=state.mode==='buy'?Number(state.user.balance):Number(state.user.holdings?.[a.symbol]||0)*a.price;
    if(document.activeElement===$('trade-percent'))$('trade-amount').value=(available*pct/100).toFixed(2);
    const amount=Math.min(raw,available),quantity=a.price>0?amount/a.price:0;
    qty.textContent=number(quantity,6)+' '+a.symbol;after.textContent=money(state.mode==='buy'?Math.max(0,Number(state.user.balance)-amount):Number(state.user.balance)+amount);
    $('trade-submit').textContent=state.mode==='buy'?'XÁC NHẬN MUA':'XÁC NHẬN BÁN';
    $('trade-submit').classList.toggle('btn-danger',state.mode==='sell');$('trade-submit').classList.toggle('btn-primary',state.mode==='buy');
  }
  async function submitTrade(){
    const a=currentAsset();if(!a)return;
    try{const r=await api('/api/trade',{method:'POST',body:{symbol:a.symbol,mode:state.mode,amount_usd:$('trade-amount').value}});setMessage('trade-message',r.message);toast(r.message);if(r.user)renderUser(r.user);await Promise.all([loadMarket(),loadPortfolio()]);}
    catch(e){setMessage('trade-message',e.message,true);}
  }
  async function loadPortfolio(){
    try{const [m,t]=await Promise.all([api('/api/market'),api('/api/trades')]);const holdings=state.user?.holdings||{};let value=0;const prices=Object.fromEntries(m.assets.map(a=>[a.symbol,a.price]));
      const rows=Object.entries(holdings).map(([sym,qty])=>{const p=prices[sym]||0,v=Number(qty||0)*p;value+=v;return {sym,qty:Number(qty||0),p,v};}).filter(x=>x.qty>0.00000001);
      $('pf-assets').textContent=money(value);$('pf-net').textContent=money(Number(state.user?.balance||0)+value);$('pf-balance').textContent=money(state.user?.balance);
      $('holdings-table').innerHTML=rows.length?table(['MÃ','SỐ LƯỢNG','GIÁ HIỆN TẠI','GIÁ TRỊ'],rows.map(x=>[x.sym,number(x.qty,6),money(x.p),money(x.v)])):'<div class="empty-state">Chưa có tài sản nào trong danh mục.</div>';
      $('trades-table').innerHTML=t.trades.length?table(['THỜI GIAN','LOẠI','MÃ','KHỐI LƯỢNG','GIÁ','GIÁ TRỊ USD'],t.trades.map(x=>[fmtDate(x.created_at),x.type,x.symbol,number(x.quantity,6),money(x.price),money(x.amount)])):'<div class="empty-state">Chưa có giao dịch.</div>';
      $('stat-assets').textContent=money(value);$('stat-networth').textContent=money(Number(state.user?.balance||0)+value);
    }catch(e){$('holdings-table').innerHTML=`<div class="empty-state">${esc(e.message)}</div>`;}
  }
  function table(headers,rows){return `<table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(x=>`<td>${esc(x)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;}
  async function loadMembership(){
    try{const s=await api('/api/membership/shop');state.shop=s;$('membership-current').textContent=s.current_label||'THÀNH VIÊN THƯỜNG';$('membership-expiry').textContent=state.user?.membership_expires_at?`Hạn đến ${fmtDate(state.user.membership_expires_at)}`:'Gói đặc biệt có thể vĩnh viễn hoặc chưa có gói.';
      const tier=state.user?.membership;const daily=s.items.find(x=>x.key===tier)?.daily||s.specials.find(x=>x.key===tier)?.daily||0;$('membership-daily').textContent=daily?money(daily)+'/ngày':'—';
      $('membership-grid').innerHTML=s.items.map(x=>`<article class="membership-card" style="border-top:2px solid ${x.color}"><h3>${esc(x.label)}</h3><small>Phân tích MA · ${x.forecast}s</small><div class="daily">${money(x.daily)}</div><small>Thưởng mỗi ngày (điều kiện nhận thưởng)</small><select class="text-input" id="plan-${x.key}">${Object.entries(x.prices).map(([k,v])=>`<option value="${k}">${esc(({three_day:'3 ngày',month:'1 tháng',year:'1 năm'})[k])} · ${x.free_trial?'MIỄN PHÍ':money(v)}</option>`).join('')}</select><button class="btn btn-primary" data-buy-tier="${x.key}">${x.free_trial?'KÍCH HOẠT THỬ':'MUA GÓI'}</button></article>`).join('');
      $('special-gifts').innerHTML=(s.specials||[]).map(x=>`<div class="special-card ${x.key==='emerald'?'emerald':''}"><b>${esc(x.label)}</b><br><span>${money(x.daily)} / ngày · ${x.key==='ruby'?'3 lượt miễn phạt':'bồi hoàn 80% khoản lỗ đủ điều kiện'}</span>${x.key==='emerald'&&isOwner()?'<button class="btn btn-quiet" data-special="emerald-self">KÍCH HOẠT OWNER</button>':''}${x.key==='ruby'&&(state.user.role==='superadmin'||isOwner())?'<button class="btn btn-quiet" data-special="ruby-test">TỰ KIỂM TRA RUBY</button>':''}<button class="btn btn-quiet" data-special-gift="${x.key}">TẶNG CHO ID ĐANG NHẬP</button></div>`).join('');
    }catch(e){$('membership-grid').innerHTML=`<div class="empty-state">${esc(e.message)}</div>`;}
  }
  async function claimBonus(){try{const r=await api('/api/membership/claim',{method:'POST',body:{}});toast(r.message);await hydrateNoPoll();}catch(e){toast(e.message,true);}}
  async function hydrateNoPoll(){await refreshMe();await Promise.all([loadPortfolio(),loadMembership()]);}
  async function giftMembership(){const body={recipient_id:$('gift-id').value.trim(),recipient_name:$('gift-name').value.trim(),tier:$('gift-tier').value,plan:$('gift-plan').value};try{const r=await api('/api/membership/gift',{method:'POST',body});setMessage('gift-message',r.message);toast(r.message);await hydrateNoPoll();}catch(e){setMessage('gift-message',e.message,true);}}
  async function loadChat(){
    try{const room=state.room;const r=await api('/api/chat/'+room);$('chat-messages').innerHTML=r.messages.map(m=>`<div class="chat-message"><b>${esc(m.name)} <span class="muted">· ${esc(m.role)}</span></b><small>${fmtDate(m.at)}</small><p>${esc(m.text)}</p></div>`).join('')||'<div class="empty-state">Chưa có tin nhắn. Hãy mở đầu cuộc trò chuyện.</div>';$('chat-messages').scrollTop=$('chat-messages').scrollHeight;$('chat-room-title').textContent=({total:'CHAT TỔNG',members:'CHAT THÀNH VIÊN',qtv:'CHAT QTV'})[room];$('chat-room-desc').textContent=({total:'Mọi tài khoản đã đăng nhập',members:'Thành viên Premium và QTV',qtv:'Khu vực trao đổi dành cho quản trị viên'})[room];}
    catch(e){$('chat-messages').innerHTML=`<div class="empty-state">${esc(e.message)}</div>`;}
  }
  async function sendChat(e){e.preventDefault();const message=$('chat-input').value.trim();if(!message)return;try{await api('/api/chat/'+state.room,{method:'POST',body:{message}});$('chat-input').value='';setMessage('chat-message','Đã gửi.');await loadChat();}catch(err){setMessage('chat-message',err.message,true);}}
  async function submitReport(){try{const r=await api('/api/reports',{method:'POST',body:{target_id:$('report-id').value.trim(),target_name:$('report-name').value.trim(),reason:$('report-reason').value.trim()}});setMessage('report-message',r.message);$('report-reason').value='';toast(r.message);}catch(e){setMessage('report-message',e.message,true);}}
  async function saveProfile(){try{const r=await api('/api/profile',{method:'PATCH',body:{display_name:$('profile-display').value,bio:$('profile-bio').value}});renderUser(r.user);setMessage('profile-message','Đã lưu hồ sơ.');}catch(e){setMessage('profile-message',e.message,true);}}
  async function transfer(){try{const r=await api('/api/transfers',{method:'POST',body:{recipient_id:$('transfer-id').value,recipient_name:$('transfer-name').value,amount:$('transfer-amount').value,mode:$('transfer-mode').value}});setMessage('transfer-message',r.message);if(r.user)renderUser(r.user);await refreshMe();}catch(e){setMessage('transfer-message',e.message,true);}}
  async function loadInbox(){try{const r=await api('/api/inbox');$('inbox-items').innerHTML=r.items.map(x=>`<div class="inbox-item ${x.read?'':'unread'}"><b>${esc(x.title)}</b><small>${fmtDate(x.created_at||x.time)}</small><p>${esc(x.body)}</p></div>`).join('')||'<div class="empty-state">Hòm thư trống.</div>';$('inbox-count').textContent=r.items.filter(x=>!x.read).length;$('inbox-count').classList.toggle('hidden',!r.items.some(x=>!x.read));}catch(e){$('inbox-items').innerHTML=`<div class="empty-state">${esc(e.message)}</div>`;}}
  async function markInboxRead(){try{await api('/api/inbox/read',{method:'POST',body:{}});await refreshMe();await loadInbox();}catch(e){toast(e.message,true);}}
  function setAdminTab(name){document.querySelectorAll('[data-admin-tab]').forEach(b=>b.classList.toggle('active',b.dataset.adminTab===name));document.querySelectorAll('.admin-content').forEach(x=>{x.classList.toggle('active',x.id==='admin-'+name);x.classList.toggle('hidden',x.id!=='admin-'+name);});}
  async function loadAdmin(){if(!isStaff())return;await loadAdminUsers();if(isOwner()){await Promise.all([loadAdminRequests(),loadReports(),loadAudit(),loadRoleLimits()]);}else{await loadReports();}}
  async function loadAdminUsers(){try{const r=await api('/api/admin/users');$('admin-users-table').innerHTML=`<table><thead><tr>${['USERNAME','ID','VAI TRÒ','THÀNH VIÊN','USD','TRẠNG THÁI','CHỌN'].map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${r.users.map(u=>`<tr><td>${esc(u.username)}</td><td>${esc(u.account_id)}</td><td>${esc(u.role_label)}</td><td>${esc(u.membership)}</td><td>${money(u.balance)}</td><td>${u.banned?'ĐÃ BAN':'HOẠT ĐỘNG'}</td><td class="table-actions"><button class="btn btn-quiet" data-pick-user="${esc(u.username)}">CHỌN</button>${isOwner()?`<button class="btn btn-danger" data-delete-user="${esc(u.username)}">XÓA</button>`:''}</td></tr>`).join('')}</tbody></table>`;}catch(e){$('admin-users-table').innerHTML=`<div class="empty-state">${esc(e.message)}</div>`;}}
  async function loadAdminRequests(){try{const r=await api('/api/admin/requests');$('admin-requests-list').innerHTML=r.requests.map(x=>`<div class="admin-request-row"><b>${esc(x.username)} · ID ${esc(x.account_id)}</b><p>${esc(x.note||'Không có ghi chú')} · ${esc(x.status)}</p><div class="row-buttons"><button class="btn btn-primary" data-request="${x.id}" data-approve="true">DUYỆT ADMIN</button><button class="btn btn-danger" data-request="${x.id}" data-approve="false">TỪ CHỐI</button></div></div>`).join('')||'<div class="empty-state">Không có yêu cầu nào.</div>';}catch(e){$('admin-requests-list').innerHTML=`<div class="empty-state">${esc(e.message)}</div>`;}}
  async function loadReports(){try{const r=await api('/api/reports');$('admin-reports-list').innerHTML=r.reports.map(x=>`<div class="report-row"><b>${esc(x.reporter)} gửi về ${esc(x.target)} · ${esc(x.type)}</b><p>${esc(x.reason)}</p><p>Trạng thái: ${esc(x.status)} · ${fmtDate(x.created_at)}</p>${isOwner()&&['pending_owner','pending_admin'].includes(x.status)?`<div class="row-buttons"><button class="btn btn-quiet" data-report="${esc(x.id)}" data-decision="${x.type==='ruby_gift_request'?'approve_ruby_gift':x.type==='ban_request'?'authorize_ban':'close'}">${x.type==='ruby_gift_request'?'DUYỆT TẶNG RUBY':x.type==='ban_request'?'CHO PHÉP BAN':'ĐÓNG HỒ SƠ'}</button><button class="btn btn-danger" data-report="${esc(x.id)}" data-decision="reject">TỪ CHỐI</button></div>`:''}${state.user?.role==='superadmin'&&x.type==='ruby_penalty_request'&&x.status==='pending_spadmin'?`<div class="row-buttons"><button class="btn btn-danger" data-spadmin-report="${esc(x.id)}" data-decision="approve">DUYỆT MỨC PHẠT</button><button class="btn btn-quiet" data-spadmin-report="${esc(x.id)}" data-decision="reject">TỪ CHỐI</button></div>`:''}</div>`).join('')||'<div class="empty-state">Không có hồ sơ nào.</div>';}catch(e){$('admin-reports-list').innerHTML=`<div class="empty-state">${esc(e.message)}</div>`;}}
  async function loadAudit(){try{const r=await api('/api/admin/audit');$('audit-list').innerHTML=table(['THỜI GIAN','NGƯỜI THỰC HIỆN','THAO TÁC','MỤC TIÊU','CHI TIẾT'],r.events.map(x=>[fmtDate(x.at),x.actor,x.action,x.target,x.details]));}catch(e){$('audit-list').innerHTML=`<div class="empty-state">${esc(e.message)}</div>`;}}
  async function loadRoleLimits(){try{const r=await api('/api/admin/role-limits');$('limit-spadmin').value=r.role_limits.superadmin;$('limit-admin').value=r.role_limits.admin;$('limit-coordinate').value=r.role_limits.coordinate;}catch(e){setMessage('limits-message',e.message,true);}}
  async function adminAction(path,body,messageId='admin-action-message'){try{const r=await api(path,{method:'POST',body});setMessage(messageId,r.message||'Đã cập nhật.');toast(r.message||'Đã cập nhật.');if(r.user && state.user && r.user.id===state.user.id)renderUser(r.user);if(isStaff())await loadAdminUsers();await refreshMe();}catch(e){setMessage(messageId,e.message,true);}}
  async function saveRoleLimits(){await adminAction('/api/admin/role-limits',{superadmin:$('limit-spadmin').value,admin:$('limit-admin').value,coordinate:$('limit-coordinate').value},'limits-message');}
  async function downloadBackup(){try{const resp=await fetch('/api/admin/backup');if(!resp.ok){const d=await resp.json();throw new Error(d.detail||'Không tải được bản sao lưu.');}const blob=await resp.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='sanchungkhoan-backup.json';a.click();URL.revokeObjectURL(url);}catch(e){toast(e.message,true);}}
  async function loadForecast(){try{const r=await api('/api/forecast');$('forecast-results').innerHTML=r.items.map(x=>`<div class="forecast-row"><b>${esc(x.symbol)}</b><span>${money(x.price)} · <strong class="${x.trend==='TĂNG'?'up':x.trend==='GIẢM'?'down':''}">${esc(x.trend)}</strong></span><small>${esc(x.confidence)} · MA horizon ${x.horizon_seconds}s</small></div>`).join('')+`<p class="muted">${esc(r.note)}</p>`;}catch(e){$('forecast-results').textContent=e.message;}}
  async function logout(){try{await api('/api/auth/logout',{method:'POST',body:{}});}catch(_){}state.user=null;if(state.poll)clearInterval(state.poll);await refreshCsrf();showAuth('login');toast('Đã đăng xuất.');}
  function bind(){
    document.querySelectorAll('[data-auth-tab]').forEach(b=>b.addEventListener('click',()=>authTab(b.dataset.authTab)));
    $('register-form').addEventListener('submit',e=>{e.preventDefault();handleAuth('register-form','/api/auth/register','Đăng ký thành công.');});
    $('login-form').addEventListener('submit',e=>{e.preventDefault();handleAuth('login-form','/api/auth/login','Đăng nhập thành công.');});
    document.addEventListener('click',async e=>{
      const nav=e.target.closest('[data-view]');if(nav){showView(nav.dataset.view);return;}
      const link=e.target.closest('[data-view-link]');if(link){showView(link.dataset.viewLink);return;}
      const asset=e.target.closest('[data-symbol]');if(asset){const a=state.market?.assets?.find(x=>x.symbol===asset.dataset.symbol);if(a)renderSelected(a);return;}
      const mode=e.target.closest('[data-mode]');if(mode){state.mode=mode.dataset.mode;document.querySelectorAll('.trade-mode-btn').forEach(x=>x.classList.toggle('active',x===mode));updateTradePreview();return;}
      const q=e.target.closest('[data-amount]');if(q){$('trade-amount').value=q.dataset.amount;updateTradePreview();return;}
      const room=e.target.closest('[data-room]');if(room){state.room=room.dataset.room;document.querySelectorAll('.chat-tab').forEach(x=>x.classList.toggle('active',x===room));loadChat();return;}
      const adminTab=e.target.closest('[data-admin-tab]');if(adminTab){setAdminTab(adminTab.dataset.adminTab);return;}
      const buy=e.target.closest('[data-buy-tier]');if(buy){const tier=buy.dataset.buyTier;try{const r=await api('/api/membership/purchase',{method:'POST',body:{tier,plan:$('plan-'+tier).value}});toast(r.message);await hydrateNoPoll();}catch(err){toast(err.message,true);}return;}
      const special=e.target.closest('[data-special]');if(special){const path=special.dataset.special==='ruby-test'?'/api/membership/ruby-self-test':'/api/membership/emerald-owner';try{const r=await api(path,{method:'POST',body:{}});toast(r.message);await hydrateNoPoll();}catch(err){toast(err.message,true);}return;}
      const pick=e.target.closest('[data-pick-user]');if(pick){$('admin-target').value=pick.dataset.pickUser;toast('Đã chọn '+pick.dataset.pickUser);return;}
      const del=e.target.closest('[data-delete-user]');if(del){if(confirm(`Xóa vĩnh viễn tài khoản ${del.dataset.deleteUser}?`))adminAction('/api/admin/users/delete',{username:del.dataset.deleteUser});return;}
      const specialGift=e.target.closest('[data-special-gift]');if(specialGift){const body={recipient_id:$('gift-id').value.trim(),recipient_name:$('gift-name').value.trim(),tier:specialGift.dataset.specialGift,plan:'month'};try{const r=await api('/api/membership/gift',{method:'POST',body});setMessage('gift-message',r.message);toast(r.message);await loadAdmin();}catch(err){setMessage('gift-message',err.message,true);}return;}
      const req=e.target.closest('[data-request]');if(req){adminAction('/api/admin/requests/'+req.dataset.request+'/decision',{approve:req.dataset.approve==='true'});await loadAdminRequests();return;}
      const rep=e.target.closest('[data-report]');if(rep){adminAction('/api/reports/'+rep.dataset.report+'/decision',{decision:rep.dataset.decision},'admin-action-message');await loadReports();return;}
      const sprep=e.target.closest('[data-spadmin-report]');if(sprep){adminAction('/api/reports/'+sprep.dataset.spadminReport+'/spadmin-decision',{decision:sprep.dataset.decision},'admin-action-message');await loadReports();return;}
    });
    $('category-filter').addEventListener('change',()=>{if(state.market)renderMarket(state.market);});
    $('trade-amount').addEventListener('input',()=>{$('trade-percent').value=10;updateTradePreview();});$('trade-percent').addEventListener('input',updateTradePreview);$('trade-submit').addEventListener('click',submitTrade);
    $('claim-bonus').addEventListener('click',claimBonus);$('gift-submit').addEventListener('click',giftMembership);$('chat-form').addEventListener('submit',sendChat);$('report-submit').addEventListener('click',submitReport);
    $('profile-save').addEventListener('click',saveProfile);$('transfer-submit').addEventListener('click',transfer);
    $('request-admin-btn').addEventListener('click',()=>adminAction('/api/role/request-admin',{note:'Xin OWNER xem xét cấp quyền ADMIN.'},'profile-message'));
    $('cancel-membership-btn').addEventListener('click',async()=>{if(!confirm('Hủy gói Premium hiện tại? Phí đã trả không được hoàn.'))return;try{const r=await api('/api/membership/cancel',{method:'POST',body:{}});setMessage('profile-message',r.message);await hydrateNoPoll();}catch(e){setMessage('profile-message',e.message,true);}});$('logout-btn').addEventListener('click',logout);
    $('portfolio-refresh').addEventListener('click',loadPortfolio);$('inbox-open').addEventListener('click',()=>{showView('profile');$('inbox-items').scrollIntoView({behavior:'smooth',block:'center'});});$('inbox-mark-read').addEventListener('click',markInboxRead);
    $('admin-users-refresh').addEventListener('click',loadAdminUsers);$('admin-ban').addEventListener('click',()=>adminAction('/api/admin/users/ban',{username:$('admin-target').value,ban:true,reason:$('admin-reason').value}));$('admin-unban').addEventListener('click',()=>adminAction('/api/admin/users/ban',{username:$('admin-target').value,ban:false,reason:$('admin-reason').value}));
    $('admin-penalty').addEventListener('click',()=>adminAction('/api/admin/users/penalty',{username:$('admin-target').value,amount:$('admin-penalty-amount').value,reason:$('admin-reason').value}));$('admin-cash-add').addEventListener('click',()=>adminAction('/api/admin/cash',{target_username:$('admin-target').value,amount:$('admin-cash-amount').value,action:'add'}));$('admin-cash-sub').addEventListener('click',()=>adminAction('/api/admin/cash',{target_username:$('admin-target').value,amount:$('admin-cash-amount').value,action:'subtract'}));
    $('admin-promote').addEventListener('click',()=>adminAction('/api/admin/roles/change',{username:$('admin-target').value,direction:'promote'}));$('admin-demote').addEventListener('click',()=>adminAction('/api/admin/roles/change',{username:$('admin-target').value,direction:'demote'}));$('admin-spadmin').addEventListener('click',()=>adminAction('/api/admin/roles/change',{username:$('admin-target').value,direction:'grant_spadmin'}));$('admin-revoke-tier').addEventListener('click',()=>adminAction('/api/membership/revoke',{username:$('admin-target').value}));
    $('limits-save').addEventListener('click',saveRoleLimits);$('backup-download').addEventListener('click',downloadBackup);$('admin-forecast').addEventListener('click',loadForecast);$('forecast-btn').addEventListener('click',()=>{if((isStaff()&&state.user?.role!=='coordinate')||state.user?.membership==='emerald'){loadForecast();showView('admin');}else toast('Bảng phân tích nâng cao dành cho QTV hoặc LỤC BẢO.',true);});
    document.addEventListener('click',e=>{const b=e.target.closest('[data-admin-request]');if(b)adminAction('/api/role/request-admin',{note:'Xin xem xét cấp quyền ADMIN.'},'profile-message');});
    window.addEventListener('resize',()=>{const a=currentAsset();if(a)drawChart(a);});
  }
  async function start(){bind();try{const status=await refreshCsrf();if(status.user){renderUser(status.user);await hydrate();}else{showAuth('register');}}catch(e){showAuth('register');$('auth-error').textContent='Không khởi tạo được: '+e.message;}}
  setInterval(()=>{$('clock').textContent=new Date().toLocaleTimeString('vi-VN');},1000);
  start();
})();
