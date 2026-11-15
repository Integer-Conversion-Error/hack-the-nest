/* Map browser pointer coordinates to the server-rendered frame, including letterboxing. */
(function(root){
  function framePoint(rect,width,height,clientX,clientY){
    const scale=Math.min(rect.width/width,rect.height/height);
    if(!Number.isFinite(scale)||scale<=0)return null;
    const x=(clientX-rect.left-(rect.width-width*scale)/2)/scale;
    const y=(clientY-rect.top-(rect.height-height*scale)/2)/scale;
    return x>=0&&y>=0&&x<=width&&y<=height?{x,y}:null;
  }
  function hitAt(scene,point){
    if(!scene||!point)return null;
    return [...scene.targets].reverse().find(t=>{const [x,y,w,h]=t.rect;return point.x>=x&&point.x<=x+w&&point.y>=y&&point.y<=y+h;})||null;
  }
  function scrubMinute(target,point,duration){return Math.max(0,Math.min(duration,(point.x-target.rect[0])/target.rect[2]*duration));}
  function resolutionFor(rect,dpr=1){
    const density=Number.isFinite(dpr)&&dpr>0?dpr:1;
    const size=Math.min(rect.width/840,rect.height/620)*density;
    return Number.isFinite(size)?Math.max(1,Math.min(3,Math.ceil(size))):1;
  }
  const api={framePoint,hitAt,scrubMinute,resolutionFor};
  if(typeof module!=='undefined')module.exports=api;else root.PygameInput=api;
})(typeof globalThis!=='undefined'?globalThis:this);
