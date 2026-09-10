import os, sys
from sim2030.presentation.server import PresentationServer
server = PresentationServer("scenarios", "runs")
server.serve(host="127.0.0.1", port=8000)
