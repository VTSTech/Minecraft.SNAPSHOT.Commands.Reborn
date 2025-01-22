#!/usr/bin/env python3
import asyncio
import csv
import logging
import os
import random
import re
import time  # Added for timestamp handling
from typing import Optional, List

# Configure logging
logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

CONFIG = {
    "java_cmd": "java -Xms512M -Xmx2G -jar server.jar nogui",
    "spawn_point": "0 64 -3",
    "rtp_radius": 35000,
    "autosave_interval": 1776,
    "autoclear_interval": 3625,
    "tpa_timeout": 30  # Added TPA timeout configuration
}

class MinecraftServer:
    def __init__(self):
        self.process: Optional[asyncio.subprocess.Process] = None
        self.tasks: List[asyncio.Task] = []
        self.command_handler = CommandHandler(self)
        
    async def start(self):
        cmd = CONFIG["java_cmd"].split()
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.info(f"Server started with PID: {self.process.pid}")
        
        self.tasks = [
            asyncio.create_task(self.handle_output()),
            asyncio.create_task(self.monitor_stderr()),
        ]

    async def handle_output(self):
        while self.process and not self.process.stdout.at_eof():
            line = (await self.process.stdout.readline()).decode().strip()
            if not line:
                continue
            
            logger.info(line)
            
            # Handle player commands
            if match := re.search(r'<([^>]+)> \.(\w+)(?:\s+(.*))?', line):
                player, command, args_str = match.groups()
                args = args_str.split() if args_str else []
                logger.info(f"Processing command: {player} > .{command} {args}")
                await self.command_handler.handle_command(player, command, args)
            
            # Handle position data response
            if match := re.search(r'(\w+) has the following entity data: \[(-?\d+\.?\d*)d, (-?\d+\.?\d*)d, (-?\d+\.?\d*)d\]', line):
                player = match.group(1)
                # Handle home positions
                if player in self.command_handler.pending_homes:
                    x = match.group(2)
                    y = match.group(3)
                    z = match.group(4)
                    self.command_handler.homes[player] = f"{x} {y} {z}"
                    self.command_handler.pending_homes.remove(player)
                    await self.command_handler.save_homes()
                    await self.command_handler.send_message(player, f"§aHome set at §eX:{x} §aY:{y} §aZ:{z}")
                # Handle warp positions
                elif player in self.command_handler.pending_warps:
                    warp_name = self.command_handler.pending_warps.pop(player)
                    x = match.group(2)
                    y = match.group(3)
                    z = match.group(4)
                    self.command_handler.warps[warp_name] = f"{x} {y} {z}"
                    await self.command_handler.save_warps()
                    await self.command_handler.send_message(player, f"§aWarp '{warp_name}' set at §eX:{x} §aY:{y} §aZ:{z}")

    async def monitor_stderr(self):
        while self.process and not self.process.stderr.at_eof():
            line = (await self.process.stderr.readline()).decode().strip()
            if line:
                logger.error(f"SERVER ERROR: {line}")

    async def execute(self, command: str):
        if self.process and self.process.stdin:
            logger.debug(f"Executing: {command}")
            self.process.stdin.write(f"{command}\n".encode())
            await self.process.stdin.drain()

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        if self.process:
            await self.execute("stop")
            await self.process.wait()

class CommandHandler:
    def __init__(self, server: MinecraftServer):
        self.server = server
        self.homes = {}
        self.warps = {}
        self.pending_homes = set()
        self.pending_warps = {}
        self.tpa_requests = {}  # Added TPA request storage
        self.homes_file = "homes.csv"
        self.warps_file = "warps.csv"
        
        # Load existing data when server starts
        asyncio.create_task(self.load_homes())
        asyncio.create_task(self.load_warps())
        asyncio.create_task(self.check_tpa_timeouts())  # Added TPA timeout task

    async def check_tpa_timeouts(self):
        """Periodically clear expired TPA requests"""
        while True:
            await asyncio.sleep(10)
            current_time = time.time()
            expired = []
            for target, (requester, timestamp) in self.tpa_requests.items():
                if current_time - timestamp > CONFIG["tpa_timeout"]:
                    expired.append(target)
            for target in expired:
                requester, _ = self.tpa_requests.pop(target)
                await self.send_message(requester, f"§cYour TPA request to {target} has expired.")
                await self.send_message(target, f"§cTPA request from {requester} has expired.")

    async def load_homes(self):
        """Load homes from CSV file"""
        try:
            if os.path.exists(self.homes_file):
                with open(self.homes_file, 'r') as f:
                    reader = csv.reader(f)
                    for row in reader:
                        if len(row) == 4:
                            player, x, y, z = row
                            self.homes[player] = f"{x} {y} {z}"
                logger.info(f"Loaded {len(self.homes)} homes from {self.homes_file}")
        except Exception as e:
            logger.error(f"Error loading homes: {str(e)}")

    async def save_homes(self):
        """Save homes to CSV file"""
        try:
            with open(self.homes_file, 'w', newline='') as f:
                writer = csv.writer(f)
                for player, pos in self.homes.items():
                    x, y, z = pos.split()
                    writer.writerow([player, x, y, z])
        except Exception as e:
            logger.error(f"Error saving homes: {str(e)}")

    async def load_warps(self):
        """Load warps from CSV file"""
        try:
            if os.path.exists(self.warps_file):
                with open(self.warps_file, 'r') as f:
                    reader = csv.reader(f)
                    for row in reader:
                        if len(row) == 4:
                            name, x, y, z = row
                            self.warps[name.lower()] = f"{x} {y} {z}"
                logger.info(f"Loaded {len(self.warps)} warps from {self.warps_file}")
            # Ensure spawn point exists
            if "spawn" not in self.warps:
                self.warps["spawn"] = CONFIG["spawn_point"]
        except Exception as e:
            logger.error(f"Error loading warps: {str(e)}")

    async def save_warps(self):
        """Save warps to CSV file"""
        try:
            with open(self.warps_file, 'w', newline='') as f:
                writer = csv.writer(f)
                for name, pos in self.warps.items():
                    # Don't save default spawn point
                    if name == "spawn" and pos == CONFIG["spawn_point"]:
                        continue
                    x, y, z = pos.split()
                    writer.writerow([name, x, y, z])
        except Exception as e:
            logger.error(f"Error saving warps: {str(e)}")

    async def handle_command(self, player: str, command: str, args: list):
        handler = getattr(self, f"cmd_{command}", None)
        if handler:
            try:
                await handler(player, *args)
            except Exception as e:
                await self.send_message(player, f"§cError: {str(e)}")
                logger.error(f"Command error: {str(e)}")
        else:
            await self.send_message(player, f"§cUnknown command: .{command}")

    # ========== TPA System Commands ========== #
    async def cmd_tpa(self, player: str, *args):
        """Send teleport request to another player"""
        if not args:
            await self.send_message(player, "§cUsage: .tpa <player>")
            return
        
        target = args[0]
        if target == player:
            await self.send_message(player, "§cYou cannot teleport to yourself!")
            return
            
        if target in self.tpa_requests:
            existing_requester, _ = self.tpa_requests[target]
            if existing_requester == player:
                await self.send_message(player, f"§cYou already have a pending request to {target}!")
                return
            
        self.tpa_requests[target] = (player, time.time())
        await self.send_message(player, f"§aTeleport request sent to {target}!")
        await self.send_message(target, f"§e{player} §ahas requested to teleport to you. Use §e.tpaccept §aor §e.tpdeny")

    async def cmd_tpaccept(self, player: str):
        """Accept teleport request"""
        if player not in self.tpa_requests:
            await self.send_message(player, "§cNo pending teleport requests!")
            return
            
        requester, _ = self.tpa_requests.pop(player)
        await self.server.execute(f"tp {requester} {player}")
        await self.send_message(player, f"§aAccepted {requester}'s teleport request!")
        await self.send_message(requester, f"§e{player} §ahas accepted your teleport request!")

    async def cmd_tpdeny(self, player: str):
        """Deny teleport request"""
        if player not in self.tpa_requests:
            await self.send_message(player, "§cNo pending teleport requests!")
            return
            
        requester, _ = self.tpa_requests.pop(player)
        await self.send_message(player, f"§cDenied {requester}'s teleport request!")
        await self.send_message(requester, f"§e{player} §cdenied your teleport request!")

    # ========== Existing Commands ========== #
    async def cmd_sethome(self, player: str):
        """Set home at current location"""
        await self.server.execute(f"data get entity {player} Pos")
        self.pending_homes.add(player)
        await self.send_message(player, "§aScanning your position...")

    async def cmd_home(self, player: str):
        """Teleport to home location"""
        if player in self.homes:
            await self.server.execute(f"tp {player} {self.homes[player]}")
            await self.send_message(player, "§aTeleported to home!")
        else:
            await self.send_message(player, "§cHome not set! Use .sethome")

    async def cmd_setwarp(self, player: str, *args):
        """Set a warp point"""
        if not args:
            await self.send_message(player, "§cUsage: .setwarp <name>")
            return
            
        warp_name = args[0].lower()
        await self.server.execute(f"data get entity {player} Pos")
        self.pending_warps[player] = warp_name
        await self.send_message(player, f"§aScanning position for warp '{warp_name}'...")

    async def cmd_warp(self, player: str, *args):
        """Teleport to a warp point"""
        if not args:
            await self.send_message(player, "§cUsage: .warp <name>")
            return
            
        warp_name = args[0].lower()
        if warp_name in self.warps:
            await self.server.execute(f"tp {player} {self.warps[warp_name]}")
            await self.send_message(player, f"§aTeleported to warp '{warp_name}'!")
        else:
            await self.send_message(player, f"§cWarp '{warp_name}' not found!")

    async def cmd_warps(self, player: str):
        """List available warps"""
        warp_list = ", ".join(self.warps.keys())
        await self.send_message(player, f"§aAvailable warps: §e{warp_list}")

    async def cmd_spawn(self, player: str):
        """Teleport to spawn"""
        await self.server.execute(f"tp {player} {self.warps['spawn']}")
        await self.send_message(player, "§aTeleported to spawn!")

    async def cmd_rtp(self, player: str):
        """Random teleport"""
        radius = CONFIG["rtp_radius"]
        x = random.randint(-radius, radius)
        z = random.randint(-radius, radius)
        await self.server.execute(f"spreadplayers {x} {z} 0 100 false {player}")
        await self.send_message(player, f"§aRandom teleported! §e(X: {x}, Z: {z})")

    async def cmd_help(self, player: str):
        """Show help menu"""
        help_msg = (
            '"=== Server Commands ===, '
            '.spawn - Teleport to spawn, '
            '.sethome - Set home, '
            '.home - Teleport home, '
            '.setwarp <name> - Set warp, '
            '.warp <name> - Teleport to warp, '
            '.warps - List warps, '
            '.rtp - Random teleport, '
            '.tpa <player> - Request teleport, '
            '.tpaccept - Accept teleport request, '
            '.tpdeny - Deny teleport request, '
            '.help - Show this menu"'
        )
        await self.server.execute(f'tell {player} {help_msg}')

    async def send_message(self, player: str, message: str):
        """Send formatted message"""
        await self.server.execute(f'tellraw {player} {{"text":"{message}"}}')

async def main():
    server = MinecraftServer()
    try:
        await server.start()
        await asyncio.Future()  # Run indefinitely
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await server.stop()

if __name__ == "__main__":
    asyncio.run(main())
