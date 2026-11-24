const tg = window.Telegram?.WebApp;
const live = Boolean(tg?.initData);
tg?.ready();
tg?.expand();
tg?.setHeaderColor('#fffdf6');
tg?.setBackgroundColor('#fffdf6');
let state = {answers: {}, question: 'family', allowed: true, subscription: {active: false, rubles: 299, stars: 0}};
let tab = 'menu';
let wizard = false;
let day = 0;
let example = false;
let busy = false;
let timer;
let selected = new Set();
const main = document.querySelector('#main');
const money = value => new Intl.NumberFormat('ru-RU', {style: 'currency', currency: 'RUB', maximumFractionDigits: 0}).format(value);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stores = ['Магнит', 'Пятёрочка', 'Перекрёсток', 'Дикси', 'Лента', 'Ашан', 'ВкусВилл', 'Чижик', 'Другой магазин'];
const quick = {
  family: ['1 взрослый', '2 взрослых', '2 взрослых и 1 ребёнок — 7 лет'],
  restriction: ['Нет', 'Да', 'Есть подозрение на реакцию'],
  traces: ['Да', 'Нет', 'Не уверены'],
  budget: ['20 000 ₽ в месяц', '30 000 ₽ в месяц', 'Не знаю'],
  period: ['На 3 дня', 'На 7 дней', 'На месяц', 'Свой вариант'],
  goals: ['Да, укажу для кого и сколько', 'Нет, достаточно калорийности порций'],
  pantry: ['Пропустить'],
};
const titles = {family:'Для кого готовим?', restriction:'Чтобы всем было хорошо', avoid:'Что исключаем?', traces:'Уточним про следы', young:'Для самых маленьких', budget:'Планируем с умом', baby_budget:'Детское питание', city:'Где вы покупаете продукты?', stores:'Ваши любимые магазины', other_store:'Как называется магазин?', period:'На сколько дней готовим?', custom_days:'Выберите свой срок', meals:'Какие приёмы пищи включить?', goals:'Считаем калории?', goal_details:'Цели взрослых', pantry:'Заглянем в холодильник'};
const familyPrompt = 'Сколько взрослых и детей в семье? Укажите возраст каждого ребёнка.\nНапример, 2 взрослых и 2 ребёнка — 1 год 8 месяцев и 7 лет.';
const demo = {
  days: [[
    {meal:'Завтрак', name:'Овсянка с яблоком и корицей', portions:[{person:'Взрослый 1',grams:280,kcal:325,serve:''}], ingredients:['Овсяные хлопья — 50 г','Молоко — 150 г','Яблоко — 80 г'], instructions:'Сварите хлопья на молоке. Яблоко нарежьте и добавьте в кашу. Посыпьте корицей.'},
    {meal:'Обед', name:'Курица с рисом и овощами', portions:[{person:'Взрослый 1',grams:380,kcal:510,serve:''}], ingredients:['Куриное филе — 150 г','Рис сухой — 60 г','Овощи — 150 г','Масло — 5 г'], instructions:'Отварите рис. Потушите курицу с овощами, добавив масло. Соедините перед подачей.'},
    {meal:'Ужин', name:'Тёплый салат с чечевицей', portions:[{person:'Взрослый 1',grams:320,kcal:390,serve:''}], ingredients:['Чечевица сухая — 65 г','Овощи — 150 г','Масло — 8 г'], instructions:'Отварите чечевицу. Добавьте нарезанные овощи и масло.'}
  ]],
  basket:{total:970,limit:2000,formula:'Пример расчёта на 3 дня.',items:[
    {id:'demo1',name:'Овсяные хлопья',store:'Магнит',packs:1,grams:400,price:80,subtotal:80,leftover:250},
    {id:'demo2',name:'Куриное филе',store:'Магнит',packs:1,grams:500,price:310,subtotal:310,leftover:50},
    {id:'demo3',name:'Овощи',store:'Магнит',packs:2,grams:500,price:145,subtotal:290,leftover:100},
    {id:'demo4',name:'Другие продукты для меню',store:'Магнит',packs:1,grams:1000,price:290,subtotal:290,leftover:0}
  ]}
};

async function api(path, body) {
  const response = await fetch('/api/' + path, {
    method: body ? 'POST' : 'GET',
    headers: {'Authorization': 'tma ' + (tg?.initData || ''), ...(body ? {'Content-Type':'application/json'} : {})},
    body: body ? JSON.stringify(body) : undefined
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
function notify(message) {
  const node = document.querySelector('#notice');
  node.textContent = message;
  node.classList.add('show');
  setTimeout(() => node.classList.remove('show'), 5000);
}
function render() {
  document.querySelectorAll('nav button').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === tab);
    b.setAttribute('aria-current', b.dataset.tab === tab ? 'page' : 'false');
  });
  document.querySelector('#cart-count').textContent = state.basket?.items.filter(i=>i.packs).length || '';
  if (tab === 'family') renderFamily();
  else if (tab === 'basket') renderBasket();
  else if (state.job === 'running') {
    main.innerHTML = '<div class="loading" role="status"><div class="loader"></div><h1>Вкусный план<br>уже на подходе</h1><p>Подбираем блюда и считаем покупки.<br>Можно закрыть приложение и вернуться позже.</p></div>';
    poll();
  } else if (wizard) renderWizard();
  else if ((state.days && !state.dirty) || example) renderMenu();
  else renderHome();
}
function renderHome() {
  main.innerHTML = '<section class="hero"><div class="hero-copy"><h1>Больше вкуса.<br>Меньше забот.</h1><p>Что приготовить? Решим вместе.<br>Меню для вас и близких с учётом бюджета и привычек.</p><button class="button primary" id="begin">' +
    (Object.keys(state.answers).length ? 'Продолжить моё меню' : 'Собрать моё меню') +
    '</button></div><div class="hero-photo-wrap"><img class="hero-photo" src="/assets/images/bowl.jpg" alt="Яркое блюдо с овощами и зеленью"><span class="photo-caption">Хороший день начинается со вкусного</span></div></section>' +
    '<div class="steps-line"><span>Блюда на каждый день</span><span>Калорийность порций</span><span>Готовый список покупок</span></div>' +
    '<div class="intro-row"><h2>Меньше «что на ужин?»</h2><button class="text-button" id="example">Пример меню</button></div>' +
    '<p class="muted">Расскажите о семье, выберите магазины — и план готов. Один человек пользуется бесплатно.</p>' +
    '<section class="tariff-strip"><div><h3>Вкуснее вместе</h3><p>Общее меню для взрослых и детей</p></div><div class="tariff-price">299 ₽<small>семейный доступ / месяц</small></div></section>';
  document.querySelector('#begin').onclick = () => {wizard = true; example = false; render();};
  document.querySelector('#example').onclick = () => {example = true; day = 0; render();};
}
function renderWizard() {
  if (!live) {
    main.innerHTML = '<div class="empty"><h1>Пойдём в Telegram?</h1><p>Откройте Mini App через кнопку бота, чтобы сохранить анкету и получить своё меню.</p><button class="button" id="demo">Посмотреть пример</button></div>';
    document.querySelector('#demo').onclick = () => {example=true;wizard=false;render();};
    return;
  }
  const q = state.question;
  if (!q && !state.prompt) {
    main.innerHTML = '<h1>Всё готово<br>для вкусного плана</h1><p>Учтём ваши ответы, посчитаем порции и соберём покупки по магазинам.</p>' +
      (state.error ? '<p class="error">' + esc(state.error) + '</p>' : '') +
      (state.allowed ? '<button class="button primary full" id="generate">Составить меню</button>' : '<div class="family-panel"><h2>За одним столом</h2><p>Для нескольких человек нужен семейный доступ.</p><div class="cost">299 ₽ <small>/ месяц</small></div><button class="button full" id="family-pay">Посмотреть подписку</button></div>') +
      '<button class="text-button" id="edit">Изменить ответы</button>';
    document.querySelector('#generate')?.addEventListener('click',()=>act({action:'generate'}));
    document.querySelector('#family-pay')?.addEventListener('click',()=>{tab='family';render();});
    document.querySelector('#edit').onclick = editAnswers;
    return;
  }
  const order = Object.keys(titles);
  const progress = Math.max(6, Math.round(order.indexOf(q) / order.length * 100));
  main.innerHTML = '<div class="question-head"><span>Знакомимся с семьёй</span><div class="progress" role="progressbar" aria-label="Заполнение анкеты" aria-valuenow="'+progress+'" aria-valuemin="0" aria-valuemax="100"><span style="width:'+progress+'%"></span></div></div>' +
    '<h1>' + esc(titles[q] || 'Уточним детали') + '</h1><p class="prompt">' + esc(state.prompt || familyPrompt) + '</p>' +
    (q === 'stores' ? '<div class="stores">' + stores.map((name,i)=>'<button class="store" data-store="'+(i+1)+'" aria-pressed="'+selected.has(i+1)+'">'+esc(name)+'</button>').join('') + '</div><div class="actions"><button class="text-button" id="all-stores">Все доступные</button><button class="button" id="done-stores">Готово</button></div>' :
      '<div class="choices">' + (quick[q] || []).map((value,i)=>'<button class="choice" data-quick="'+i+'">'+esc(value)+'</button>').join('') + '</div>') +
    '<form id="answer-form"><label for="answer">Ваш ответ</label><textarea id="answer" maxlength="3500" required placeholder="'+(q==='family'?'Например, 2 взрослых и ребёнок 5 лет':'Можно ответить своими словами')+'"></textarea><button type="submit" class="button primary full">Продолжить</button></form>' +
    '<p><small>Ответы сохраняются и передаются OpenAI для подбора меню. Удалить анкету можно в разделе «Семья».</small></p>';
  document.querySelector('#answer-form').onsubmit = event => {
    event.preventDefault();
    const text = document.querySelector('#answer').value.trim();
    if (text) act({action:'answer',text});
  };
  document.querySelectorAll('[data-quick]').forEach(button=>button.onclick=()=>{
    const i=Number(button.dataset.quick);
    const numeric = ['restriction','traces','period','goals'].includes(q);
    act({action:'answer',text:numeric ? String(i+1) : quick[q][i]});
  });
  document.querySelectorAll('[data-store]').forEach(button=>button.onclick=()=>{
    const i=Number(button.dataset.store);
    selected.has(i)?selected.delete(i):selected.add(i);
    button.setAttribute('aria-pressed',selected.has(i));
  });
  document.querySelector('#all-stores')?.addEventListener('click',()=>{
    for(let i=1;i<=8;i++) selected.add(i);
    renderWizard();
  });
  document.querySelector('#done-stores')?.addEventListener('click',()=>{
    if (!selected.size) return notify('Выберите хотя бы один магазин.');
    act({action:'stores',stores:[...selected]});
  });
}
async function act(data) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('main button, main textarea').forEach(el=>el.disabled=true);
  try {
    state = await api('action',data);
    selected = new Set();
    render();
  } catch(error) {
    notify(error.message);
    document.querySelectorAll('main button, main textarea').forEach(el=>el.disabled=false);
  } finally {busy=false;}
}
function poll() {
  clearTimeout(timer);
  if (!live) return;
  timer = setTimeout(async()=>{
    try {
      state = await api('state');
      if (state.job === 'done') wizard=false;
      render();
    } catch(error) {notify(error.message);poll();}
  },3000);
}
function renderMenu() {
  const source = example ? demo : state;
  day = Math.min(day,source.days.length-1);
  main.innerHTML = (example ? '<p class="demo-note">Пример интерфейса. Блюда, калории и покупки показаны для знакомства.</p>' : '') +
    '<div class="intro-row"><h1>Сегодня<br>будет вкусно</h1><button class="text-button" id="change">Изменить</button></div>' +
    '<div class="day-tabs" aria-label="Дни меню">' + source.days.map((_,i)=>'<button class="'+(i===day?'active':'')+'" data-day="'+i+'" aria-pressed="'+(i===day)+'">День '+(i+1)+'</button>').join('')+'</div>' +
    source.days[day].map((meal,i)=>'<article class="meal"><p class="meal-label">'+esc(meal.meal)+'</p><h2 style="margin:0">'+esc(meal.name)+'</h2><div class="portions">'+meal.portions.map(p=>'<span class="portion">'+esc(p.person)+' · '+(p.grams?esc(p.grams)+' г · '+esc(p.kcal)+' ккал':'по режиму кормления')+'</span>').join('')+'</div>' +
    meal.portions.filter(p=>p.serve).map(p=>'<p><small>'+esc(p.person)+'. '+esc(p.serve)+'</small></p>').join('') +
    '<button class="recipe-toggle" data-recipe="'+i+'" aria-expanded="false">Как приготовить</button><div class="recipe" id="recipe-'+i+'" hidden><h3>На всю семью</h3><ul>'+meal.ingredients.map(s=>'<li>'+esc(s)+'</li>').join('')+'</ul><p>'+esc(meal.instructions)+'</p></div></article>').join('')+
    '<div class="daily-total"><strong>За день по включённым приёмам</strong><br>'+dailyTotals(source.days[day])+'</div>' +
    '<p><small>Калорийность и готовый вес справочные. Детям дневные лимиты не назначаются.</small></p>'+
    (!example ? '<details><summary>Полный расчёт и итог по бюджету</summary><pre>'+esc(state.report.join('\n\n'))+'</pre></details>':'');
  document.querySelectorAll('[data-day]').forEach(b=>b.onclick=()=>{day=Number(b.dataset.day);render();});
  document.querySelectorAll('[data-recipe]').forEach(b=>b.onclick=()=>{
    const body=document.querySelector('#recipe-'+b.dataset.recipe);
    body.hidden=!body.hidden;b.setAttribute('aria-expanded',!body.hidden);
    b.textContent=body.hidden?'Как приготовить':'Свернуть рецепт';
  });
  document.querySelector('#change').onclick=()=>example?(example=false,render()):editAnswers();
}
function dailyTotals(meals) {
  const totals={};
  meals.forEach(m=>m.portions.forEach(p=>{if(p.grams) totals[p.person]=(totals[p.person]||0)+p.kcal;}));
  return Object.entries(totals).map(([person,total])=>esc(person)+' — '+total+' ккал').join('<br>');
}
function renderBasket() {
  const source=example?demo:state;
  if (!source.basket || state.dirty && !example) {
    main.innerHTML='<div class="empty"><img src="/assets/images/salad.jpg" alt="Свежие овощи и зелень"><h1>За покупками<br>с готовым планом</h1><p>Сначала составим меню, затем соберём продукты по магазинам с учётом домашних запасов.</p><button class="button" id="go-menu">Собрать меню</button></div>';
    document.querySelector('#go-menu').onclick=()=>{tab='menu';wizard=true;render();};return;
  }
  const basket=source.basket;
  const groups=[...new Set(basket.items.filter(i=>i.packs).map(i=>i.store))];
  const storageKey='shopping-'+(example?'demo':state.plan_date+JSON.stringify(basket.items));
  let checked=new Set();
  try {checked=new Set(JSON.parse(sessionStorage.getItem(storageKey)||'[]'));} catch {}
  main.innerHTML='<h1>Купим только<br>нужное</h1>'+
    (example?'<p class="demo-note">Демонстрационный список покупок.</p>':'')+
    '<div class="budget"><div>Корзина целыми упаковками</div><div class="budget-amount">'+money(basket.total)+'</div><p>'+(basket.limit===null?'Бюджет не задан':(basket.total<=basket.limit?'Останется в бюджете '+money(basket.limit-basket.total):'Выше лимита на '+money(basket.total-basket.limit)))+'</p><small>'+esc(basket.formula)+'</small></div>'+
    groups.map(store=>'<h2>'+esc(store)+'</h2>'+basket.items.filter(i=>i.store===store&&i.packs).map(item=>'<div class="shopping-item '+(checked.has(item.id)?'done':'')+'"><input type="checkbox" id="item-'+esc(item.id)+'" data-check="'+esc(item.id)+'" '+(checked.has(item.id)?'checked':'')+'><label for="item-'+esc(item.id)+'">'+esc(item.name)+'<small>'+item.packs+' уп. × '+item.grams+' г · '+money(item.price)+' за уп.</small></label><span class="shopping-price">'+money(item.subtotal)+'</span></div>').join('')).join('')+
    '<p><small>Цены ориентировочные. Наличие и скидки не проверены. Проверьте состав и маркировку аллергенов на упаковках.</small></p>';
  document.querySelectorAll('[data-check]').forEach(input=>input.onchange=()=>{
    input.checked?checked.add(input.dataset.check):checked.delete(input.dataset.check);
    input.closest('.shopping-item').classList.toggle('done',input.checked);
    try {sessionStorage.setItem(storageKey,JSON.stringify([...checked]));} catch {}
  });
}
function renderFamily() {
  const family=state.answers.family;
  const subscription=state.subscription;
  main.innerHTML='<h1>Каждому —<br>свою порцию</h1><p>Общий план, разные вкусы. Учитываем возраст, ограничения и привычки каждого.</p>'+
    '<div class="facts"><div><dt>Ваша семья</dt><dd>'+(family?family.adults+' взр. · '+family.children+' дет.':'Ещё не познакомились')+'</dd></div><div><dt>Город</dt><dd>'+esc(state.answers.city||'Укажите в анкете')+'</dd></div><div><dt>Для одного человека</dt><dd>Бесплатно</dd></div></div>'+
    '<button class="text-button" id="edit-profile">Изменить ответы</button>'+
    '<section class="family-panel"><h2>Вкуснее вместе</h2><div class="cost">299 ₽ <small>/ месяц</small></div><ul><li>Меню для нескольких человек</li><li>Отдельные порции для взрослых и детей</li><li>Общий список покупок и бюджет</li></ul>'+
    (subscription.active?'<p><strong>Доступ активен до '+new Date(subscription.expires*1000).toLocaleDateString('ru-RU')+'</strong></p>'+(subscription.canceled?'<p>Автопродление отключено.</p>':'<button class="button full" id="cancel">Отключить автопродление</button>')+'':
      '<button class="button full" id="pay" '+(!live||!subscription.stars?'disabled':'')+'>'+(subscription.stars?'Подключить за '+subscription.stars+' Stars':'Оплата скоро появится')+'</button>')+
    '<p><small>Подписка на 30 дней с автопродлением. Оплата в Telegram Stars. Стоимость Stars в рублях зависит от способа покупки. Точная сумма списания показана перед оплатой.</small></p></section>'+
    '<div class="policy"><h3>Ваши данные</h3><p><small>Анкета хранится на сервере бота и передаётся OpenAI для составления меню. Удаление анкеты удаляет меню. Подписка и платёжные записи сохраняются, автопродление отключается отдельно.</small></p>'+
    '<div class="actions"><button class="text-button danger" id="delete">Удалить анкету и меню</button><button class="text-button" id="reset">Начать заново</button></div><p><small>По вопросам оплаты и возврата отправьте боту /paysupport.</small></p></div>';
  document.querySelector('#edit-profile').onclick=editAnswers;
  document.querySelector('#pay')?.addEventListener('click',pay);
  document.querySelector('#cancel')?.addEventListener('click',async()=>{
    if(!confirm('Отключить следующее списание? Доступ сохранится до конца оплаченного периода.'))return;
    try{await api('cancel-subscription',{});state=await api('state');render();notify('Автопродление отключено.');}catch(e){notify(e.message);}
  });
  for(const action of ['delete','reset']) document.querySelector('#'+action).onclick=async()=>{
    if(!live)return notify('Откройте приложение через Telegram.');
    if(!confirm(action==='delete'?'Удалить анкету и меню?':'Заменить текущую анкету новой?'))return;
    await act({action});tab='menu';wizard=true;example=false;render();
  };
}
function editAnswers() {
  if(!live)return notify('Личная анкета доступна через Telegram.');
  main.innerHTML='<h1>Что изменилось?</h1><p>Напишите, какой ответ исправить или какое блюдо заменить. Например, «Бюджет теперь 30 000 ₽» или «Замени обед второго дня».</p><form id="edit-form"><label for="edit-text">Изменение</label><textarea id="edit-text" maxlength="3500" required></textarea><button class="button primary full">Сохранить</button></form><button class="text-button" id="back">Вернуться</button>';
  document.querySelector('#back').onclick=render;
  document.querySelector('#edit-form').onsubmit=async event=>{
    event.preventDefault();const text=document.querySelector('#edit-text').value.trim();
    if(!text)return;
    wizard=true;tab='menu';await act({action:'answer',text});
  };
}
async function pay(){
  if(busy||!live)return;busy=true;
  const button=document.querySelector('#pay');button.disabled=true;
  try{
    const invoice=await api('invoice',{});
    tg.openInvoice(invoice.url,async status=>{
      busy=false;if(button.isConnected)button.disabled=false;
      if(status==='paid'){notify('Платёж получен. Обновляем доступ…');await refreshPayment(5);}
      else if(status==='failed')notify('Платёж не прошёл. Попробуйте ещё раз.');
    });
  }catch(error){busy=false;button.disabled=false;notify(error.message);}
}
async function refreshPayment(attempts){
  try{state=await api('state');render();if(state.subscription.active)return;}catch(e){notify(e.message);}
  if(attempts>0)setTimeout(()=>refreshPayment(attempts-1),2000);
  else notify('Подтверждение ещё обрабатывается. Откройте приложение чуть позже.');
}
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{
  tab=button.dataset.tab;render();window.scrollTo({top:0});tg?.HapticFeedback?.selectionChanged();
});
async function boot(){
  if(live){
    try{state=await api('state');wizard=Object.keys(state.answers).length>0&&!state.days;render();}
    catch(error){main.innerHTML='<h1>Не удалось загрузить меню</h1><p class="error">'+esc(error.message)+'</p><button class="button" id="retry">Попробовать снова</button>';document.querySelector('#retry').onclick=boot;}
  }else{document.querySelector('#preview-note').hidden=false;render();}
}
boot();
