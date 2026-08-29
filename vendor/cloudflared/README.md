# Cloudflare Tunnel component

Release builds download `cloudflared-windows-amd64.exe` version **2026.5.2**
from the official Cloudflare GitHub release and require this SHA-256 before it
can enter the application package:

`20b9638f685333d623798e733effbad2487093f15ba592f6c7752360ff3b7ab7`

The executable is intentionally not checked into source control. The compiled
Windows application starts it only after a school has been registered and an
active installation authorization has been verified. The tunnel token is
passed through the child environment, never a command-line argument.
