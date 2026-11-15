"""Call publish_frame(surface) from your Pygame loop, approximately 5–10 times/sec."""
import io
from urllib.request import Request, urlopen


def publish_frame(surface, dashboard_url='http://127.0.0.1:8000'):
    import pygame
    buffer = io.BytesIO()
    pygame.image.save(surface, buffer, 'frame.png')
    request = Request(dashboard_url.rstrip('/') + '/api/frame', buffer.getvalue(),
                      {'Content-Type': 'image/png'})
    with urlopen(request, timeout=2) as response:
        return response.status == 200
