(() => {
 const mode=document.getElementById('period-mode40');
 if(!mode)return;
 const first=document.querySelector('[name=semester_label]'), duration=document.querySelector('[name=duration_semesters]');
 const start=document.querySelector('[name=mobility_start_date]'), end=document.querySelector('[name=mobility_end_date]');
 const result=document.getElementById('return-period41'), priority=document.getElementById('period-priority41'), feedback=document.getElementById('period-feedback41');
 const terms=JSON.parse(document.getElementById('calendar-terms41').textContent);
 function index(value){const m=/^(\d{4})\/([12])$/.exec(value.trim());return m?Number(m[1])*2+Number(m[2])-1:null;}
 function label(n){return String(Math.floor(n/2)).padStart(4,'0')+'/'+(n%2+1);}
 function recalculate(){
  priority.value='duration';feedback.textContent='';
  result.readOnly=mode.value==='dates';result.required=false;
  if(mode.value==='dates'){
   const matches=terms.filter(t=>end.value&&t.start_date<=end.value&&end.value<=t.end_date);
   result.value=matches.length===1?matches[0].semester:'';
   if(end.value&&matches.length!==1)feedback.textContent='A data de fim deve corresponder a um único período no calendário acadêmico.';
  } else {
   const n=index(first.value),d=Number(duration.value);
   result.value=n!==null&&Number.isInteger(d)&&d>=1&&d<=100?label(n+d):'';
  }
 }
 [first,duration,start,end].forEach(el=>el.addEventListener('input',recalculate));
 mode.addEventListener('change',recalculate);
 result.addEventListener('input',()=>{
  if(mode.value==='dates')return;
  priority.value='return';const a=index(first.value),b=index(result.value);
  feedback.textContent='';
  if(a!==null&&b!==null){const d=b-a;if(d>=1&&d<=100)duration.value=d;else feedback.textContent='O semestre de retorno deve ser posterior ao início, com duração máxima de 100 semestres.';}
 });
 recalculate();
})();
