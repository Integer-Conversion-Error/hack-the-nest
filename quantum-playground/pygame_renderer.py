"""Render the current website schedule to PNG using offscreen Pygame surfaces."""
import io
import os
import math
from threading import Lock
from fleet_handoff import depot_for, replay_depots

RENDER_LOCK = Lock()
COLORS = ['#8af1d0', '#a0abff', '#ffc177', '#fa8faf', '#73c9ff', '#c1df72']
FRAME_SIZE = (840, 620)
MAP_RECT = (63, 56, 714, 378)
CARD_COLUMNS = 4
CARD_TOP = 440
CARD_HEIGHT = 68
CARD_ROW_STEP = 78
FONT_CACHE = {}


def map_point(sim, position):
    block_width, block_height = block_size(sim)
    return round(90 + position[0] * block_width), round(410 - position[1] * block_height)


def block_size(sim):
    grid = sim['grid']
    return 660 / (grid['width'] - 1), 330 / (grid['height'] - 1)


def map_density(sim):
    width, height = block_size(sim)
    return min(1, width / 132, height / 66)


def stop_radius(sim):
    clearance = int(min(block_size(sim)) / 2) - 1
    return max(3, min(20, clearance, round(30 * map_density(sim))))


def van_card(index):
    row, column = divmod(index, CARD_COLUMNS)
    return 24 + column * 198, CARD_TOP + row * CARD_ROW_STEP, 188, CARD_HEIGHT


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


def vehicle_layout(sim, minute):
    density = map_density(sim)
    trucks = {}
    shared_locations = {}
    for van in sim['vans']:
        van_id = van['id']
        position, battery = state_at(van, minute, depot_for(sim, van))
        center = map_point(sim, position)
        angle = truck_angle(van, minute)
        dimensions = (52, 40) if abs(angle) in (0, 180) else (40, 52)
        size = tuple(max(12, round(length * density)) for length in dimensions)
        trucks[van_id] = (center, size, angle, battery)
        shared_locations.setdefault(center, []).append(van_id)

    left, top, width, height = MAP_RECT
    sprite_margin = max(6, math.ceil(26 * density))
    step = max(16, 2 * sprite_margin + 4)
    for (x, y), van_ids in shared_locations.items():
        if len(van_ids) < 2:
            continue
        columns = min(3, len(van_ids))
        rows = math.ceil(len(van_ids) / columns)
        horizontal_margin = step * (columns - 1) / 2 + sprite_margin
        vertical_margin = step * (rows - 1) / 2 + sprite_margin
        x = min(max(x, left + horizontal_margin), left + width - horizontal_margin)
        y = min(max(y, top + vertical_margin), top + height - vertical_margin)
        for index, van_id in enumerate(van_ids):
            row, column = divmod(index, columns)
            row_width = min(columns, len(van_ids) - row * columns)
            dx = (column - (row_width - 1) / 2) * step
            dy = (row - (rows - 1) / 2) * step
            _, size, angle, battery = trucks[van_id]
            trucks[van_id] = ((round(x + dx), round(y + dy)), size, angle, battery)
    return trucks


def scene_layout(sim, minute, *, trucks=None):
    """Hit regions describe the exact frame, independent of browser scaling."""
    def point(position):
        return map_point(sim, position)
    targets = []
    radius = stop_radius(sim)
    card_rows = max(1, math.ceil(len(sim['vans']) / CARD_COLUMNS))
    panel_shift = (card_rows - 1) * CARD_ROW_STEP
    if trucks is None:
        trucks = vehicle_layout(sim, minute)
    for kind, objects in [('delivery', sim['deliveries']), ('charger', sim['chargers'])]:
        for item in objects:
            x, y = point(item['position'])
            targets.append(dict(kind=kind, id=item['id'], rect=[x-radius,y-radius,2*radius,2*radius]))
    for i, van in enumerate(sim['vans']):
        (x, y), (width, height), _, _ = trucks[van['id']]
        targets.append(dict(kind='van', id=van['id'], rect=[x-width/2,y-height/2,width,height]))
        targets.append(dict(kind='van', id=van['id'], rect=list(van_card(i))))
    for kind, x, width in [('play',24,100), ('restart',134,100), ('back',244,70), ('forward',324,70), ('speed',404,90), ('clear',504,110)]:
        targets.append(dict(kind=kind, rect=[x,530+panel_shift,width,34]))
    targets.append(dict(kind='scrub', rect=[24,580+panel_shift,792,24]))
    return dict(width=FRAME_SIZE[0], height=FRAME_SIZE[1]+panel_shift, minute=minute, targets=targets)


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

        trucks = vehicle_layout(sim, minute)
        scene = scene_layout(sim, minute, trucks=trucks)
        frame_size = (scene['width'], scene['height'])
        panel_shift = scene['height'] - FRAME_SIZE[1]
        art=Art(frame_size)
        art.surface.fill('#0a1220')
        xy=lambda point:map_point(sim,point)
        grid=sim['grid']
        density = map_density(sim)
        radius = stop_radius(sim)
        minute_total=round(sim['start_hour']*60+minute)
        art.rect('#142234',(24,14,30,30),radius=8)
        art.lines('#72e4e1',[(32,35),(39,21),(46,35)],2)
        art.line('#72e4e1',(35,30),(43,30),2)
        art.text('FLEET MAP',(65,12),'#e1edf7',28)
        art.text('Click a truck or stop to explore.',(66,36),'#7692aa',16)
        art.rect('#142b35',(624,13,192,31),radius=8)
        art.circle('#68dfbb',(639,28),3)
        art.text(f'{minute_total//60:02d}:{minute_total%60:02d}',(654,21),'#a5eee0',20)
        art.rect('#101d2b', MAP_RECT, radius=16)
        art.rect('#294154', MAP_RECT, width=1, radius=16)
        for x in range(72,772,14):
            for y in range(66,430,14):art.circle('#1c3040',(x,y),.65)

        # City blocks use low-profile architecture and illuminated roof details.
        for gx in range(grid['width']-1):
            for gy in range(grid['height']-1):
                x,y=xy([gx+.5,gy+.5])
                if density < 1:
                    park = (gx + gy) % 5 == 0
                    art.rect('#183334' if park else '#23364a', (x-36*density,y-16*density,72*density,32*density), radius=3)
                    art.rect('#254c43' if park else '#324c62', (x-28*density,y-10*density,56*density,20*density), radius=2)
                    continue
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
        for color, width in (('#304456', 34), ('#1c2c3d', 30), ('#263747', 24)):
            for a,b in roads:art.line(color,a,b,max(1,round(width*density)))
        block_width, block_height = block_size(sim)
        x_labels = math.ceil(24 / block_width)
        y_labels = math.ceil(20 / block_height)
        for x in range(grid['width']):
            for y in range(grid['height']-1):
                a,b=xy([x,y]),xy([x,y+1])
                for mark in range(int(b[1]+19*density),int(a[1]-12*density),max(6,round(16*density))):art.line('#83929a',(a[0],mark),(a[0],mark+5*density))
            if x % x_labels == 0:
                a=xy([x,0]);art.text(x,(a[0],a[1]+21),'#718ca5',16,True)
        for y in range(grid['height']):
            for x in range(grid['width']-1):
                a,b=xy([x,y]),xy([x+1,y])
                for mark in range(int(a[0]+19*density),int(b[0]-12*density),max(6,round(16*density))):art.line('#83929a',(mark,a[1]),(mark+5*density,a[1]))
            if y % y_labels == 0:
                a=xy([0,y]);art.text(y,(a[0]-24,a[1]),'#718ca5',16,True)
        for gx in range(grid['width']):
            x,y=xy([gx,min(2,grid['height']-1)])
            for stripe in range(-9,10,6):art.rect('#8699a5',(x+19*density,y+stripe*density,5*density,2*density),radius=1)

        for i,van in enumerate(sim['vans']):
            if selected and selected!=van['id']:continue
            color = COLORS[i % len(COLORS)]
            for e in van['events']:
                if e['type']!='drive':continue
                points=[xy(p) for p in e['path']]
                tint=pygame.Color(color)
                dim=tuple(int(c*.26) for c in tint[:3])
                art.lines(dim,points,max(1,round((8 if selected else 5)*density)))
                art.lines(color if selected else tuple(int(c*.55) for c in tint[:3]),points,2 if selected else 1)
        done={e['delivery_id'] for v in sim['vans'] for e in v['events'] if e['type']=='deliver' and e['end_min']<=min(minute,sim['duration_min'])}
        for delivery in sim['deliveries']:
            x,y=xy(delivery['position']);complete=delivery['id'] in done;color='#76dcb6' if complete else '#c2cddd'
            if density < 1:
                art.circle('#152a37', (x, y), radius)
                art.circle(color, (x, y), radius, 1)
                if radius >= 9:
                    art.text(delivery['id'], (x, y), color, 16, True)
                continue
            art.circle('#152a37',(x,y),17);art.circle('#406575' if complete else '#496073',(x,y),17,1)
            art.rect('#24493e' if complete else '#384858',(x-9,y-10,18,17),radius=3)
            art.lines(color,[(x-8,y-5),(x,y-1),(x+8,y-5)],1)
            art.line(color,(x,y-1),(x,y+6),1)
            if complete:art.lines('#7ee7c2',[(x+6,y-12),(x+9,y-9),(x+15,y-16)],2)
            art.text(delivery['id'],(x,y+15),'#d6e3ef',16,True)
        for depot in replay_depots(sim):
            x,y=xy(depot['position'])
            if density < 1:
                art.rect('#1a3044', (x-radius, y-radius, 2*radius, 2*radius), radius=4)
                art.rect('#5a8da5', (x-radius, y-radius, 2*radius, 2*radius), width=1, radius=4)
                if radius >= 9:
                    art.text(depot['id'] if 'depots' in sim else 'HQ', (x, y), '#c2ecf2', 16, True)
            else:
                art.rect('#1a3044',(x-22,y-19,44,36),radius=6)
                art.rect('#5a8da5',(x-22,y-19,44,36),width=1,radius=6)
                art.rect('#284b61',(x-15,y-14,30,8),radius=2)
                for dx in (-10,-2,6):art.line('#68b3bd',(x+dx,y-13),(x+dx,y-7))
                art.rect('#112535',(x-12,y-1,24,18),radius=2)
                art.text(depot['id'] if 'depots' in sim else 'HQ',(x,y+7),'#c2ecf2',18,True)
        for charger in sim['chargers']:
            x,y=xy(charger['position'])
            active=any(e['type']=='charge' and e.get('charger_id')==charger['id'] and e['start_min']<=minute<e['end_min'] for v in sim['vans'] for e in v['events'])
            color='#f4c477' if active else '#61dec8'
            if density < 1:
                if active:art.glow(color,(x,y),radius)
                art.rect('#123638', (x-radius, y-radius, 2*radius, 2*radius), radius=4)
                art.rect(color, (x-radius, y-radius, 2*radius, 2*radius), width=1, radius=4)
                if radius >= 9:
                    art.text(charger['id'], (x, y), color, 16, True)
                continue
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
            (x, y), size, angle, battery = trucks[van['id']]
            color = COLORS[i % len(COLORS)]
            if selected==van['id']:
                art.glow(color,(x,y),24*density)
                for dx,dy in ((-1,-1),(-1,1),(1,-1),(1,1)):
                    art.lines(color,[(x+dx*29*density,y+dy*19*density),(x+dx*29*density,y+dy*28*density),(x+dx*20*density,y+dy*28*density)],1)
            image=truck(color,angle,i+1)
            pixel_size = art.point(size)
            if image.get_size() != pixel_size:
                image = pygame.transform.smoothscale(image, pixel_size)
            art.surface.blit(image,image.get_rect(center=art.point((x,y))))
            pct=battery/sim['battery_capacity_kwh']*100;card=van_card(i)
            card_x, card_y, _, _ = card
            art.rect('#121f2f',card,radius=9);art.rect('#3d6a78' if selected==van['id'] else '#24374b',card,width=1,radius=9)
            art.rect(color,(card_x+1,card_y+10,3,26),radius=1)
            art.text(van['id'],(card_x+12,card_y+9),color,20)
            art.text(f'{pct:.0f}%',(card_x+149,card_y+9),'#dbe8f3',22)
            active=next((e for e in van['events'] if e['start_min']<=minute<e['end_min']),None)
            activity={'drive':'Driving','charge':'Charging','wait':'Waiting','deliver':'Delivering'}
            art.text(f"{activity[active['type']] if active else 'Parked'} / {battery:.1f} kWh",(card_x+12,card_y+31),'#7f9cb4',16)
            art.rect('#24364a',(card_x+12,card_y+55,164,3),radius=1)
            art.rect('#f38a9d' if pct<sim['reserve_pct'] else color,(card_x+12,card_y+55,164*max(0,min(100,pct))/100,3),radius=1)
        labels=dict(play='Pause' if playing else 'Play',restart='Restart',back='-5 min',forward='+5 min',speed=f'{speed:g}x',clear='Show all')
        for target in scene['targets']:
            if target['kind'] in labels:
                x,y,w,h=target['rect'];art.rect('#162b39' if target['kind']=='play' else '#121f2f',(x,y,w,h),radius=7)
                art.rect('#477b85' if target['kind']=='play' else '#2b4157',(x,y,w,h),width=1,radius=7)
                art.text(labels[target['kind']],(x+w/2,y+h/2),'#a7e6e2' if target['kind']=='play' else '#a0b9cf',18,True)
        art.text(f'{len(done):02d}/{len(sim["deliveries"]):02d} DELIVERED',(650,543+panel_shift),'#6fd5bb',18)
        cursor=24+792*minute/sim['duration_min']
        scrub_y = 592 + panel_shift
        art.line('#233b50',(24,scrub_y),(816,scrub_y),3);art.line('#5abdbb',(24,scrub_y),(cursor,scrub_y),3)
        art.glow('#74eee2',(cursor,scrub_y),4);art.circle('#c2fff2',(cursor,scrub_y),5)
        output_size=tuple(dimension*resolution for dimension in frame_size)
        final=art.surface if art.surface.get_size()==output_size else pygame.transform.smoothscale(art.surface,output_size)
        buffer=io.BytesIO();pygame.image.save(final,buffer,'frame.png')
        return buffer.getvalue()
