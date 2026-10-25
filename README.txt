AC Link - play Assetto Corsa with a friend over Tailscale
=========================================================

Needs (both players): Assetto Corsa (Steam), Content Manager, Custom Shaders Patch, Tailscale.

ONE-TIME SETUP
0. Run AC Link.exe. The first time it shows what's missing; click Download next to AssettoServer.
1. Both: install Tailscale (tailscale.com/download) and log in, each with your own account.
2. Host: open login.tailscale.com > Machines > click the "..." next to your PC > Share, and send it to your friend.
   Friend: accept the share. (Your PC then shows up in their Tailscale; they can't see the rest of your network.)
3. Host: the first time you host, Windows asks to let AssettoServer and AC Link through the firewall. Allow both
   (AC Link uses port 8082 to share content).
   If AC Link shows "Windows Firewall is blocking AssettoServer", click Fix firewall.

PLAYING
Host:  open AC Link > host > pick a track, time, weather, traffic and the cars your friend can use > Host.
       Give your friend the 8-character code. Click "Join my game" to drive too.
Guest: open AC Link > join > type the code > Find > Join. Content Manager opens the server: pick your car, press Join.

Missing something? The join screen lists what the host has that you don't (or have in a different version) and
offers to download it straight from the host's PC over Tailscale, with progress and resume. Official Kunos content
(base game and DLC) is never shared: get it from Steam. Paid or no-redistribution mods: the host's call.

Closing AC Link while hosting asks first, because it ends the server. Joining a game minimizes AC Link.

Advanced: settings has every AssettoServer option (from its own schema), server rules, traffic, plugins, CSP
options, the generated config files and the live server log.

Files: settings.json (your choices), server.log (last server run), vendor\assettoserver (AssettoServer
0.0.55-pre45, downloaded on first launch from github.com/compujuckel/AssettoServer, AGPL-3.0).
Full guide with links and screenshots: github.com/r0mn-creator/AC-Link
