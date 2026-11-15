"""Render the current website schedule to PNG using offscreen Pygame surfaces."""
import io
import os
import math
from threading import Lock

RENDER_LOCK = Lock()
COLORS = ['#8af1d0', '#a0abff', '#ffc177', '#fa8faf']
FRAME_SIZE = (840, 620)
FONT_CACHE = {}


def map_point(sim, position):
    grid = sim['grid']
    return round(90+position[0]*660/(grid['width']-1)), round(80+(grid['height']-1-position[1])*330/(grid['height']-1))


def truck_angle(van, minute):
    drives = [e for e in van['events'] if e['type']=='drive' and e['start_min']<=minute]
    if not drives:
        return 0
    event = drives[-1]
    t = min(1,max(0,(minute-event['start_min'])/(event['end_min']-event['start_min'] or 1)))
    path = event['path']
    index = min(int(t*(len(path)-1)),len(path)-2)
    a,b = path[index:index+2]
    return math.degrees(math.atan2(b[1]-a[1],b[0]-a[0]))


class PygameUnavailable(RuntimeError):
    pass


def state_at(van, minute, depot):
    position, battery = depot, van['initial_battery_kwh']
    for event in van['events']:
        if minute < event['start_min']:
            break
        duration = event['end_min']-event['start_min']
        fraction = min(1, max(0, (minute-event['start_min'])/duration)) if duration else 1
        battery = event['battery_start_kwh'] + fraction*(event['battery_end_kwh']-event['battery_start_kwh'])
        position = event['position']
        if event['type'] == 'drive':
            path = event['path']
            segment = fraction*(len(path)-1)
            index = min(int(segment), len(path)-2)
            part = segment-index
            position = [a+(b-a)*part for a, b in zip(path[index], path[index+1])]
        if minute < event['end_min']:
            break
    return position, battery


def scene_layout(sim, minute):
    """Hit regions describe the exact frame, independent of browser scaling."""
    def point(position):
        return map_point(sim, position)
    targets = []
    for kind, objects in [('delivery', sim['deliveries']), ('charger', sim['chargers'])]:
        for item in objects:
            x, y = point(item['position'])
            targets.append(dict(kind=kind, id=item['id'], rect=[x-20,y-20,40,40]))
    for i, van in enumerate(sim['vans']):
        position, _ = state_at(van, minute, sim['depot'])
        x, y = point(position)
        offset = round((i-1.5)*7)
        width,height = (52,40) if abs(truck_angle(van,minute)) in (0,180) else (40,52)
        targets.append(dict(kind='van', id=van['id'], rect=[x-width//2+offset,y-height//2+offset,width,height]))
        targets.append(dict(kind='van', id=van['id'], rect=[24+i*198,440,188,68]))
    for kind, x, width in [('play',24,100), ('restart',134,100), ('back',244,70), ('forward',324,70), ('speed',404,90), ('clear',504,110)]:
        targets.append(dict(kind=kind, rect=[x,530,width,34]))
    targets.append(dict(kind='scrub', rect=[24,580,792,24]))
    return dict(width=FRAME_SIZE[0], height=FRAME_SIZE[1], minute=minute, targets=targets)


def render_png(sim, minute, selected=None, playing=False, speed=4, resolution=2):
    """Output real 1x/2x/3x pixels; keep hit targets in logical coordinates."""
    if isinstance(resolution, bool) or resolution not in (1,2,3):
        raise ValueError('Resolution must be 1, 2, or 3.')
    os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
    try:
        import pygame
    except ImportError as exc:
        raise PygameUnavailable('Pygame is not installed in the Python environment running server.py. Activate that environment and run: python3 -m pip install pygame. Then restart server.py.') from exc
    if sim.get('kind') != 'ev_fleet' or not sim.get('vans'):
        raise ValueError('Generate an EV dispatch before opening the Pygame view.')
    with RENDER_LOCK:
        pygame.font.init()
        scale = max(2,resolution)
        for size in (16,18,20,22,28):
            if (scale,size) not in FONT_CACHE:
                FONT_CACHE[scale,size] = pygame.font.Font(None,size*scale)
        fonts = {size: FONT_CACHE[scale,size] for size in (16,18,20,22,28)}

        class Art:
            def __init__(self,size,alpha=False):
                self.surface=pygame.Surface((size[0]*scale,size[1]*scale),pygame.SRCALPHA if alpha else 0)
            def point(self,p): return tuple(round(v*scale) for v in p)
            def rect(self,color,box,width=0,radius=0):
                pygame.draw.rect(self.surface,color,tuple(round(v*scale) for v in box),width*scale,border_radius=radius*scale)
            def line(self,color,a,b,width=1):
                pygame.draw.line(self.surface,color,self.point(a),self.point(b),width*scale)
            def lines(self,color,points,width=1,closed=False):
                pygame.draw.lines(self.surface,color,closed,[self.point(p) for p in points],width*scale)
            def circle(self,color,point,radius,width=0):
                pygame.draw.circle(self.surface,color,self.point(point),round(radius*scale),width*scale)
            def polygon(self,color,points):
                pygame.draw.polygon(self.surface,color,[self.point(p) for p in points])
            def text(self,value,point,color='#9bb3c7',size=18,center=False):
                image=fonts[size].render(str(value),True,color)
                pos=self.point(point)
                self.surface.blit(image,image.get_rect(center=pos) if center else pos)
            def glow(self,color,point,radius):
                tint=pygame.Color(color)
                layer=pygame.Surface(self.surface.get_size(),pygame.SRCALPHA)
                for extra,alpha in ((7,12),(4,22),(1,40)):
                    pygame.draw.circle(layer,(*tint[:3],alpha),self.point(point),round((radius+extra)*scale))
                self.surface.blit(layer,(0,0))

        art=Art(FRAME_SIZE)
        art.surface.fill('#0a1220')
        xy=lambda point:map_point(sim,point)
        grid=sim['grid']
        minute_total=round(sim['start_hour']*60+minute)
        art.rect('#142234',(24,14,30,30),radius=8)
        art.lines('#72e4e1',[(32,35),(39,21),(46,35)],2)
        art.line('#72e4e1',(35,30),(43,30),2)
        art.text('FLEET MAP',(65,12),'#e1edf7',28)
        art.text('Click a truck or stop to explore.',(66,36),'#7692aa',16)
        art.rect('#142b35',(624,13,192,31),radius=8)
        art.circle('#68dfbb',(639,28),3)
        art.text(f'{minute_total//60:02d}:{minute_total%60:02d}',(654,21),'#a5eee0',20)
        art.rect('#101d2b',(63,56,714,378),radius=16)
        art.rect('#294154',(63,56,714,378),width=1,radius=16)
        for x in range(72,772,14):
            for y in range(66,430,14):art.circle('#1c3040',(x,y),.65)

        # City blocks use low-profile architecture and illuminated roof details.
        for gx in range(grid['width']-1):
            for gy in range(grid['height']-1):
                x,y=xy([gx+.5,gy+.5])
                if (gx+gy)%5==0:
                    art.rect('#183334',(x-36,y-16,72,32),radius=7)
                    art.lines('#315b52',[(x-31,y+9),(x,y-9),(x+31,y+9)],1)
                    for dx in (-21,0,21):
                        art.circle('#254c43',(x+dx,y),7)
                        art.circle('#39735e',(x+dx-1,y-2),4)
                else:
                    art.rect('#0b1422',(x-34,y-16,75,37),radius=5)
                    art.rect('#23364a',(x-39,y-19,72,34),radius=5)
                    art.rect('#324c62',(x-39,y-19,72,34),width=1,radius=5)
                    art.rect('#192b3e',(x-31,y-13,54,21),radius=3)
                    for dx in (-25,-11,3):
                        art.rect('#315a70',(x+dx,y-10,10,14),radius=1)
                        art.line('#4b8196',(x+dx+2,y-9),(x+dx+8,y-9),1)
                    art.line('#638893',(x-29,y+12),(x+19,y+12),1)
                    art.rect('#579087',(x+29,y-8,3,13),radius=1)
        # Curbs and road shoulders frame real two-way streets.
        roads=[]
        for x in range(grid['width']):roads.append((xy([x,0]),xy([x,grid['height']-1])))
        for y in range(grid['height']):roads.append((xy([0,y]),xy([grid['width']-1,y])))
        for a,b in roads:art.line('#304456',a,b,34)
        for a,b in roads:art.line('#1c2c3d',a,b,30)
        for a,b in roads:art.line('#263747',a,b,24)
        for x in range(grid['width']):
            for y in range(grid['height']-1):
                a,b=xy([x,y]),xy([x,y+1])
                for mark in range(int(b[1]+19),int(a[1]-12),16):art.line('#83929a',(a[0],mark),(a[0],mark+5))
            a=xy([x,0]);art.text(x,(a[0],a[1]+21),'#718ca5',16,True)
        for y in range(grid['height']):
            for x in range(grid['width']-1):
                a,b=xy([x,y]),xy([x+1,y])
                for mark in range(int(a[0]+19),int(b[0]-12),16):art.line('#83929a',(mark,a[1]),(mark+5,a[1]))
            a=xy([0,y]);art.text(y,(a[0]-24,a[1]),'#718ca5',16,True)
        for gx in range(grid['width']):
            x,y=xy([gx,min(2,grid['height']-1)])
            for stripe in range(-9,10,6):art.rect('#8699a5',(x+19,y+stripe,5,2),radius=1)

        for i,van in enumerate(sim['vans']):
            if selected and selected!=van['id']:continue
            for e in van['events']:
                if e['type']!='drive':continue
                points=[xy(p) for p in e['path']]
                tint=pygame.Color(COLORS[i%4])
                dim=tuple(int(c*.26) for c in tint[:3])
                art.lines(dim,points,8 if selected else 5)
                art.lines(COLORS[i%4] if selected else tuple(int(c*.55) for c in tint[:3]),points,2 if selected else 1)
        done={e['delivery_id'] for v in sim['vans'] for e in v['events'] if e['type']=='deliver' and e['end_min']<=min(minute,sim['duration_min'])}
        for delivery in sim['deliveries']:
            x,y=xy(delivery['position']);complete=delivery['id'] in done;color='#76dcb6' if complete else '#c2cddd'
            art.circle('#152a37',(x,y),17);art.circle('#406575' if complete else '#496073',(x,y),17,1)
            art.rect('#24493e' if complete else '#384858',(x-9,y-10,18,17),radius=3)
            art.lines(color,[(x-8,y-5),(x,y-1),(x+8,y-5)],1)
            art.line(color,(x,y-1),(x,y+6),1)
            if complete:art.lines('#7ee7c2',[(x+6,y-12),(x+9,y-9),(x+15,y-16)],2)
            art.text(delivery['id'],(x,y+15),'#d6e3ef',16,True)
        x,y=xy(sim['depot'])
        art.rect('#1a3044',(x-22,y-19,44,36),radius=6)
        art.rect('#5a8da5',(x-22,y-19,44,36),width=1,radius=6)
        art.rect('#284b61',(x-15,y-14,30,8),radius=2)
        for dx in (-10,-2,6):art.line('#68b3bd',(x+dx,y-13),(x+dx,y-7))
        art.rect('#112535',(x-12,y-1,24,18),radius=2)
        art.text('HQ',(x,y+7),'#c2ecf2',18,True)
        for charger in sim['chargers']:
            x,y=xy(charger['position'])
            active=any(e['type']=='charge' and e.get('charger_id')==charger['id'] and e['start_min']<=minute<e['end_min'] for v in sim['vans'] for e in v['events'])
            color='#f4c477' if active else '#61dec8'
            if active:art.glow(color,(x,y),18)
            art.rect('#123638',(x-20,y-20,40,40),radius=7)
            art.rect(color,(x-20,y-20,40,40),width=1,radius=7)
            art.rect('#b1cfcd',(x-9,y-15,16,27),radius=4)
            art.rect('#1f555f',(x-6,y-12,10,8),radius=2)
            art.polygon('#ddfff1',[(x,y-1),(x-4,y+5),(x,y+5),(x-2,y+10),(x+5,y+2),(x+1,y+2)])
            art.lines('#6aafb1',[(x+7,y-8),(x+13,y-4),(x+13,y+9),(x+9,y+9)],2)
            art.text(charger['id'],(x,y+15),'#b8f7df',16,True)

        def truck(color,angle,number):
            sprite=Art((52,40),True)
            sprite.rect((0,0,0,90),(3,10,47,25),radius=7)
            for box in ((9,5,10,6),(9,29,10,6),(36,7,8,5),(36,28,8,5)):sprite.rect('#080f17',box,radius=2)
            sprite.rect('#abc3d3',(4,9,30,22),radius=5)
            sprite.rect('#20364c',(6,11,26,18),radius=4)
            sprite.line(color,(9,12),(29,12),2)
            sprite.line('#577083',(9,27),(29,27),1)
            sprite.rect('#b6cddc',(32,11,16,19),radius=5)
            sprite.rect('#254455',(36,13,9,13),radius=3)
            sprite.line('#67dce3',(42,15),(42,23),2)
            sprite.rect('#d7fbff',(46,12,3,4),radius=1)
            sprite.rect('#d7fbff',(46,25,3,4),radius=1)
            sprite.rect('#fa9c99',(3,13,2,4),radius=1)
            sprite.rect('#fa9c99',(3,24,2,4),radius=1)
            sprite.rect(color,(15,16,11,8),radius=2)
            sprite.line('#1e3e4d',(20,16),(20,23))
            sprite.text(number,(10,20),'#c8dce9',16,True)
            return pygame.transform.rotate(sprite.surface,angle)

        for i,van in enumerate(sim['vans']):
            position,battery=state_at(van,minute,sim['depot']);x,y=xy(position);offset=round((i-1.5)*7);color=COLORS[i%4]
            if selected==van['id']:
                art.glow(color,(x+offset,y+offset),24)
                for dx,dy in ((-1,-1),(-1,1),(1,-1),(1,1)):
                    art.lines(color,[(x+offset+dx*29,y+offset+dy*19),(x+offset+dx*29,y+offset+dy*28),(x+offset+dx*20,y+offset+dy*28)],1)
            image=truck(color,truck_angle(van,minute),i+1)
            art.surface.blit(image,image.get_rect(center=art.point((x+offset,y+offset))))
            pct=battery/sim['battery_capacity_kwh']*100;card=(24+i*198,440,188,68)
            art.rect('#121f2f',card,radius=9);art.rect('#3d6a78' if selected==van['id'] else '#24374b',card,width=1,radius=9)
            art.rect(color,(card[0]+1,450,3,26),radius=1)
            art.text(van['id'],(card[0]+12,449),color,20)
            art.text(f'{pct:.0f}%',(card[0]+149,449),'#dbe8f3',22)
            active=next((e for e in van['events'] if e['start_min']<=minute<e['end_min']),None)
            activity={'drive':'Driving','charge':'Charging','wait':'Waiting','deliver':'Delivering'}
            art.text(f"{activity[active['type']] if active else 'Parked'} / {battery:.1f} kWh",(card[0]+12,471),'#7f9cb4',16)
            art.rect('#24364a',(card[0]+12,495,164,3),radius=1)
            art.rect('#f38a9d' if pct<sim['reserve_pct'] else color,(card[0]+12,495,164*max(0,min(100,pct))/100,3),radius=1)
        labels=dict(play='Pause' if playing else 'Play',restart='Restart',back='-5 min',forward='+5 min',speed=f'{speed:g}x',clear='Show all')
        for target in scene_layout(sim,minute)['targets']:
            if target['kind'] in labels:
                x,y,w,h=target['rect'];art.rect('#162b39' if target['kind']=='play' else '#121f2f',(x,y,w,h),radius=7)
                art.rect('#477b85' if target['kind']=='play' else '#2b4157',(x,y,w,h),width=1,radius=7)
                art.text(labels[target['kind']],(x+w/2,y+h/2),'#a7e6e2' if target['kind']=='play' else '#a0b9cf',18,True)
        art.text(f'{len(done):02d}/{len(sim["deliveries"]):02d} DELIVERED',(650,543),'#6fd5bb',18)
        cursor=24+792*minute/sim['duration_min']
        art.line('#233b50',(24,592),(816,592),3);art.line('#5abdbb',(24,592),(cursor,592),3)
        art.glow('#74eee2',(cursor,592),4);art.circle('#c2fff2',(cursor,592),5)
        # Preserve detail instead of shrinking every frame back to 840 x 620.
        output_size=tuple(dimension*resolution for dimension in FRAME_SIZE)
        final=art.surface if art.surface.get_size()==output_size else pygame.transform.smoothscale(art.surface,output_size)
        buffer=io.BytesIO();pygame.image.save(final,buffer,'frame.png')
        return buffer.getvalue()
