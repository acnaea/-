import discord
from discord.ext import commands
from discord.ui import View, Select, Button, Modal, TextInput
import time
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Match the exact variable name used on Railway
ALLOWED_ROLE_ID = int(os.getenv("ALLOWED_ROLE_ID", 1500947284526108763))

# Global dictionaries to track active rooms separately
active_scrims = {}
active_tryouts = {}

server_lists = {
    'blacklist': set(),  
    'whitelist': set()   
}

ALL_PLAYSTYLES = [
    discord.SelectOption(label="Egoist", emoji="👑"),
    discord.SelectOption(label="Speedster", emoji="⚡"),
    discord.SelectOption(label="Monster", emoji="👹"),
    discord.SelectOption(label="Glam", emoji="✨"),
    discord.SelectOption(label="Demon", emoji="😈"),
    discord.SelectOption(label="Lazy Genius", emoji="💤"),
    discord.SelectOption(label="Guardian", emoji="🛡️"),
    discord.SelectOption(label="Destroyer", emoji="💥"),
    discord.SelectOption(label="Prodigy", emoji="💎")
]

# Original position variables for Scrims
SCRIM_POSITIONS = ["CF", "RW", "LW", "CM", "GK"]


def has_staff_perms(member: discord.Member):
    """Check if member has the specific staff role or admin rights"""
    if member.guild_permissions.administrator:
        return True
    return any(role.id == ALLOWED_ROLE_ID for role in member.roles)


def format_team_list(team_players, max_size):
    """Formats a team roster list cleanly based on the tryout size"""
    lines = []
    for i in range(max_size):
        if i < len(team_players):
            user_id, style = team_players[i]
            lines.append(f"> **#{i+1}** | <@{user_id}> {style.lower()}")
        else:
            lines.append(f"> **#{i+1}** | ")
    return "\n".join(lines) if lines else "> *Empty*"


def generate_scrim_embed(scrim_id):
    """Generates the original Position-based layout for Scrims with description header"""
    scrim = active_scrims.get(scrim_id)
    if not scrim:
        return None, False
        
    lineup = scrim['lineup']
    description_text = "# SCRIM LINEUP\n"
    
    for pos in SCRIM_POSITIONS:
        if lineup[pos]:
            user_id, style_str = lineup[pos]
            style_lower = style_str.lower()
            description_text += f"> **{pos:<2}** |  <@{user_id}> {style_lower}\n"
        else:
            description_text += f"> **{pos:<2}** |  \n"
            
    filled_count = sum(1 for val in lineup.values() if val is not None)
    is_full = filled_count >= 5
    
    embed = discord.Embed(description=description_text, color=discord.Color.dark_embed())
    embed.set_footer(text=f"{filled_count}/5 positions filled")
    embed.set_image(url="https://cdn.discordapp.com/attachments/1177079104739213403/1177079546680459354/WHITE.gif?ex=6a17ae41&is=6a165cc1&hm=7ab266d368bed34e0b779ac589760a69bdf0523dd6342a64fb50e581195b7322")
    return embed, is_full


def generate_tryout_embed(tryout_id):
    """Generates the Team Versus layout for Tryouts"""
    tryout = active_tryouts.get(tryout_id)
    if not tryout:
        return None, False
        
    players = tryout['players']
    size = tryout['size']
    
    team1 = players[:size]
    team2 = players[size : size * 2]
    
    embed = discord.Embed(title=f"{size}V{size} TRYOUT LINEUP", color=discord.Color.dark_embed())
    embed.add_field(name="🔵 Team 1", value=format_team_list(team1, size), inline=True)
    embed.add_field(name="🔴 Team 2", value=format_team_list(team2, size), inline=True)
    
    filled_count = len(players)
    max_players = size * 2
    is_full = filled_count >= max_players
    
    embed.set_footer(text=f"{filled_count}/{max_players} players joined")
    embed.set_image(url="https://cdn.discordapp.com/attachments/1177079104739213403/1177079546680459354/WHITE.gif?ex=6a17ae41&is=6a165cc1&hm=7ab266d368bed34e0b779ac589760a69bdf0523dd6342a64fb50e581195b7322")
    return embed, is_full


# --- ADMIN CONTROL PANEL ---

class AdminActionModal(Modal):
    def __init__(self, action_type):
        super().__init__(title=f"Admin {action_type.title()} Tool")
        self.action_type = action_type
        
        self.user_input = TextInput(
            label="User ID",
            placeholder="Paste target Discord user ID here...",
            min_length=15,
            max_length=20,
            required=True
        )
        self.add_item(self.user_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            target_id = int(self.user_input.value.strip())
        except ValueError:
            await interaction.followup.send("❌ Invalid ID format.", ephemeral=True)
            return

        if self.action_type == "blacklist":
            server_lists['whitelist'].discard(target_id)
            server_lists['blacklist'].add(target_id)
            
            for s_id, scrim in active_scrims.items():
                for pos, val in scrim['lineup'].items():
                    if val and val[0] == target_id:
                        scrim['lineup'][pos] = None
                        await refresh_main_board(interaction.guild, s_id, is_scrim=True)
            for t_id, tryout in active_tryouts.items():
                if any(p[0] == target_id for p in tryout['players']):
                    tryout['players'] = [p for p in tryout['players'] if p[0] != target_id]
                    await refresh_main_board(interaction.guild, t_id, is_scrim=False)

            await interaction.followup.send(f"🚫 User `<@{target_id}>` blacklisted.", ephemeral=True)

        elif self.action_type == "whitelist":
            server_lists['blacklist'].discard(target_id)
            server_lists['whitelist'].add(target_id)
            await interaction.followup.send(f"✅ User `<@{target_id}>` whitelisted.", ephemeral=True)


class AdminScrimKickDropdown(Select):
    def __init__(self, room_id, is_scrim, active_players):
        self.room_id = room_id
        self.is_scrim = is_scrim
        options = [discord.SelectOption(label=p['name'], value=p['value']) for p in active_players]
        super().__init__(placeholder="Select a player to kick...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        if self.is_scrim:
            room = active_scrims.get(self.room_id)
            if room:
                room['lineup'][self.values[0]] = None
        else:
            room = active_tryouts.get(self.room_id)
            if room:
                target_id = int(self.values[0])
                room['players'] = [p for p in room['players'] if p[0] != target_id]
        
        await refresh_main_board(interaction.guild, self.room_id, self.is_scrim)
        await interaction.followup.send("👢 Kicked player out of the session.", ephemeral=True)


class AdminControlPanelDashboard(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="👢 Kick Player", style=discord.ButtonStyle.primary, custom_id="admin_kick_btn")
    async def admin_kick(self, interaction: discord.Interaction, button: discord.Button):
        if not has_staff_perms(interaction.user):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return

        active_players = []
        for s_id, s_data in active_scrims.items():
            for pos, val in s_data['lineup'].items():
                if val:
                    member = interaction.guild.get_member(val[0])
                    name = member.display_name if member else f"ID: {val[0]}"
                    active_players.append({'name': f"Scrim: {name} ({pos})", 'value': pos, 'room_id': s_id, 'is_scrim': True})
        
        for t_id, t_data in active_tryouts.items():
            for p in t_data['players']:
                member = interaction.guild.get_member(p[0])
                name = member.display_name if member else f"ID: {p[0]}"
                active_players.append({'name': f"Tryout: {name}", 'value': str(p[0]), 'room_id': t_id, 'is_scrim': False})

        if not active_players:
            await interaction.response.send_message("ℹ️ No active players found.", ephemeral=True)
            return

        view = View(timeout=60)
        view.add_item(AdminScrimKickDropdown(active_players[0]['room_id'], active_players[0]['is_scrim'], active_players))
        await interaction.response.send_message("Select player:", view=view, ephemeral=True)


# --- SELECTION & INTERACTION FLOWS ---

async def refresh_main_board(guild, room_id, is_scrim):
    room = active_scrims.get(room_id) if is_scrim else active_tryouts.get(room_id)
    if not room:
        return
    try:
        main_channel = guild.get_channel(room['channel_id'])
        board_msg = await main_channel.fetch_message(room['board_msg_id'])
        embed, _ = generate_scrim_embed(room_id) if is_scrim else generate_tryout_embed(room_id)
        await board_msg.edit(embed=embed)
    except Exception as e:
        print(f"Error syncing board: {e}")


class StyleSelectionDropdown(Select):
    def __init__(self, room_id, is_scrim, position_choice=None):
        self.room_id = room_id
        self.is_scrim = is_scrim
        self.position_choice = position_choice
        
        room = active_scrims.get(room_id) if is_scrim else active_tryouts.get(room_id)
        
        taken_styles = set()
        if room:
            items = room['lineup'].values() if is_scrim else room['players']
            for val in items:
                if val:
                    taken_styles.add(val[1])
        
        available_styles = []
        for option in ALL_PLAYSTYLES:
            if option.label in taken_styles:
                continue
            if is_scrim and position_choice == "GK" and option.label == "Glam":
                continue
            available_styles.append(option)
            
        super().__init__(
            placeholder="Pick your play style...", 
            options=available_styles if available_styles else [discord.SelectOption(label="No Styles Available")]
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        room = active_scrims.get(self.room_id) if self.is_scrim else active_tryouts.get(self.room_id)
        if not room:
            await interaction.followup.send("⚠️ Expired.", ephemeral=True)
            return
            
        style_choice = self.values[0]
        if style_choice == "No Styles Available":
            return

        guild = interaction.guild
        
        if self.is_scrim:
            taken_styles = [val[1] for val in room['lineup'].values() if val]
            if style_choice in taken_styles:
                await interaction.followup.send("⚠️ Style taken.", ephemeral=True)
                return
                
            room['lineup'][self.position_choice] = (interaction.user.id, style_choice)
            
            try:
                main_channel = guild.get_channel(room['channel_id'])
                board_msg = await main_channel.fetch_message(room['board_msg_id'])
                embed, is_full = generate_scrim_embed(self.room_id)
                
                if is_full:
                    mentions = " ".join([f"<@{val[0]}>" for val in room['lineup'].values() if val])
                    content_update = f"✅ **{room['region']} SCRIM IS FULL!** {mentions}"
                    await board_msg.edit(content=content_update, embed=embed, view=MainQueueView(self.room_id, is_scrim=True, full=True))
                else:
                    await board_msg.edit(embed=embed)
            except Exception as e: print(e)
            
            await interaction.followup.edit_message(
                message_id=interaction.message.id,
                content=f"✅ **Roster Joined!**\n**Position:** {self.position_choice} | **Style:** {style_choice}",
                view=None
            )
            
        else:
            taken_styles = [val[1] for val in room['players']]
            if style_choice in taken_styles:
                await interaction.followup.send("⚠️ Style taken.", ephemeral=True)
                return
                
            if len(room['players']) >= (room['size'] * 2):
                await interaction.followup.send("⚠️ Queue is full.", ephemeral=True)
                return

            room['players'].append((interaction.user.id, style_choice))
            
            try:
                main_channel = guild.get_channel(room['channel_id'])
                board_msg = await main_channel.fetch_message(room['board_msg_id'])
                embed, is_full = generate_tryout_embed(self.room_id)
                
                if is_full:
                    mentions = " ".join([f"<@{p[0]}>" for p in room['players']])
                    content_update = f"✅ **{room['size']}V{room['size']} TRYOUT IS FULL!** {mentions}"
                    await board_msg.edit(content=content_update, embed=embed, view=MainQueueView(self.room_id, is_scrim=False, full=True))
                else:
                    await board_msg.edit(embed=embed)
            except Exception as e: print(e)
            
            await interaction.followup.edit_message(
                message_id=interaction.message.id,
                content=f"✅ **Joined Team Queue!**\n**Style:** {style_choice}",
                view=None
            )


class PositionSelectionDropdown(Select):
    def __init__(self, room_id):
        self.room_id = room_id
        scrim = active_scrims.get(room_id)
        
        available_options = []
        if scrim:
            for pos_name in SCRIM_POSITIONS:
                if scrim['lineup'][pos_name] is None:
                    available_options.append(discord.SelectOption(label=pos_name))
                    
        super().__init__(
            placeholder="Pick your position...", 
            options=available_options if available_options else [discord.SelectOption(label="Full")]
        )

    async def callback(self, interaction: discord.Interaction):
        chosen_position = self.values[0]
        if chosen_position == "Full":
            await interaction.response.send_message("⚠️ Slots filled.", ephemeral=True)
            return
            
        next_view = View(timeout=60)
        next_view.add_item(StyleSelectionDropdown(self.room_id, is_scrim=True, position_choice=chosen_position))
        
        await interaction.response.edit_message(
            content=f"✅ **Position:** {chosen_position}\n**Step 2/2** — Pick your play style:",
            view=next_view
        )


class MainQueueView(View):
    def __init__(self, room_id, is_scrim, full=False):
        super().__init__(timeout=None)
        prefix = "scrim" if is_scrim else "tryout"
        
        if not full:
            self.add_item(Button(label="⚽ Join Match", style=discord.ButtonStyle.primary, custom_id=f"join_{prefix}_{room_id}"))
            self.add_item(Button(label="❌ Leave", style=discord.ButtonStyle.danger, custom_id=f"leave_{prefix}_{room_id}"))
        self.add_item(Button(label="🗑️ Cancel", style=discord.ButtonStyle.secondary, custom_id=f"cancel_{prefix}_{room_id}"))


@bot.event
async def on_ready():
    # Register global view listener so buttons don't break on bot reboot
    bot.add_view(AdminControlPanelDashboard())
    print(f"Logged in as {bot.user.name} - Railway Integration Active")


@bot.event
async def on_interaction(interaction: discord.Interaction):
    if not interaction.data or "custom_id" not in interaction.data:
        return
        
    custom_id = interaction.data["custom_id"]
    guild = interaction.guild
    user_id = interaction.user.id
    
    # --- JOIN SYSTEM ---
    if custom_id.startswith("join_scrim_") or custom_id.startswith("join_tryout_"):
        is_scrim = "scrim_" in custom_id
        room_id = custom_id.replace("join_scrim_", "") if is_scrim else custom_id.replace("join_tryout_", "")
        room = active_scrims.get(room_id) if is_scrim else active_tryouts.get(room_id)
        
        if not room:
            await interaction.response.send_message("⚠️ Expired.", ephemeral=True)
            return

        if user_id in server_lists['blacklist']:
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return

        if str(user_id) in room['cooldowns']:
            expiry = room['cooldowns'][str(user_id)]
            remaining = int(expiry - time.time())
            if remaining > 0:
                await interaction.response.send_message(f"⏳ **Cooldown:** Wait **{remaining}s**.", ephemeral=True)
                return
            else:
                del room['cooldowns'][str(user_id)]
            
        if is_scrim:
            if any(val and val[0] == user_id for val in room['lineup'].values()):
                await interaction.response.send_message("⚠️ Already joined.", ephemeral=True)
                return
            
            view = View(timeout=60)
            view.add_item(PositionSelectionDropdown(room_id))
            await interaction.response.send_message(
                content="**Step 1/2** — Pick your position:",
                view=view,
                ephemeral=True
            )
        else:
            if any(p[0] == user_id for p in room['players']):
                await interaction.response.send_message("⚠️ Already joined.", ephemeral=True)
                return
            if len(room['players']) >= (room['size'] * 2):
                await interaction.response.send_message("⚠️ Room full.", ephemeral=True)
                return
                
            view = View(timeout=60)
            view.add_item(StyleSelectionDropdown(room_id, is_scrim=False))
            await interaction.response.send_message(
                content="Pick your play style to finalize entry:",
                view=view,
                ephemeral=True
            )
        
    # --- LEAVE SYSTEM ---
    elif custom_id.startswith("leave_scrim_") or custom_id.startswith("leave_tryout_"):
        is_scrim = "scrim_" in custom_id
        room_id = custom_id.replace("leave_scrim_", "") if is_scrim else custom_id.replace("leave_tryout_", "")
        room = active_scrims.get(room_id) if is_scrim else active_tryouts.get(room_id)
        if not room: return
            
        if is_scrim:
            found_pos = None
            for pos, val in room['lineup'].items():
                if val and val[0] == user_id:
                    found_pos = pos
                    break
            if found_pos:
                room['lineup'][found_pos] = None
                room['cooldowns'][str(user_id)] = time.time() + 5.0
                await interaction.response.defer()
                await refresh_main_board(guild, room_id, is_scrim=True)
            else:
                await interaction.response.send_message("⚠️ Not in lineup.", ephemeral=True)
        else:
            if any(p[0] == user_id for p in room['players']):
                room['players'] = [p for p in room['players'] if p[0] != user_id]
                room['cooldowns'][str(user_id)] = time.time() + 5.0
                await interaction.response.defer()
                await refresh_main_board(guild, room_id, is_scrim=False)
            else:
                await interaction.response.send_message("⚠️ Not in lineup.", ephemeral=True)

    # --- CANCEL SYSTEM ---
    elif custom_id.startswith("cancel_scrim_") or custom_id.startswith("cancel_tryout_"):
        is_scrim = "scrim_" in custom_id
        room_id = custom_id.replace("cancel_scrim_", "") if is_scrim else custom_id.replace("cancel_tryout_", "")
        room = active_scrims.get(room_id) if is_scrim else active_tryouts.get(room_id)
        
        if not has_staff_perms(interaction.user):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
            
        await interaction.response.defer()
        if room:
            try:
                board_msg = await interaction.channel.fetch_message(room['board_msg_id'])
                await board_msg.edit(content="❌ **CANCELED**", embed=None, view=None)
            except Exception:
                pass
                
            if is_scrim:
                active_scrims.pop(room_id, None)
            else:
                active_tryouts.pop(room_id, None)


# --- COMMANDS ---

@bot.command()
@commands.has_permissions(administrator=True)
async def permission(ctx, role: discord.Role = None):
    """View or set the designated staff role dynamically in memory"""
    global ALLOWED_ROLE_ID
    
    if role is None:
        await ctx.send(f"ℹ️ Current staff role permission is set to: <@&{ALLOWED_ROLE_ID}> (ID: `{ALLOWED_ROLE_ID}`)")
        return
        
    ALLOWED_ROLE_ID = role.id
    await ctx.send(f"✅ **Permission Updated!** Staff commands are now restricted to: {role.mention}\n*Note: To save this permanently across server restarts, please update your ALLOWED_ROLE_ID variable inside your Railway Dashboard!*")


@bot.command()
async def panel(ctx):
    if not has_staff_perms(ctx.author):
        await ctx.send("❌ Denied", delete_after=5)
        return
        
    await ctx.message.delete()
    embed = discord.Embed(
        title="🛠️ Scrim/Tryout Control Center",
        color=discord.Color.dark_red()
    )
    await ctx.send(embed=embed, view=AdminControlPanelDashboard(), delete_after=300)


@bot.command()
async def scrim(ctx, region: str = None):
    if not has_staff_perms(ctx.author):
        await ctx.send("❌ Denied", delete_after=5)
        return

    if region is None or region.upper() not in ["EU", "NA", "AS"]:
        await ctx.send("❌ Use: `!scrim <EU/NA/AS>`", delete_after=10)
        return

    await ctx.message.delete()
    scrim_id = str(ctx.message.id)
    region_upper = region.upper()
    
    active_scrims[scrim_id] = {
        'board_msg_id': None,
        'channel_id': ctx.channel.id,
        'region': region_upper,
        'cooldowns': {},  
        'lineup': {pos: None for pos in SCRIM_POSITIONS}
    }
        
    embed, _ = generate_scrim_embed(scrim_id)
    content_text = f"🔹 **{region_upper} SCRIM** | Hosted by **{ctx.author.display_name}**\nPick your position and style to join!"
    board_msg = await ctx.send(content=content_text, embed=embed, view=MainQueueView(scrim_id, is_scrim=True))
    active_scrims[scrim_id]['board_msg_id'] = board_msg.id


@bot.command()
async def tryout(ctx, size: int = None):
    if not has_staff_perms(ctx.author):
        await ctx.send("❌ Denied", delete_after=5)
        return

    if size is None or size not in [2, 3, 4, 5]:
        await ctx.send("❌ Use: `!tryout <2/3/4/5>`", delete_after=10)
        return

    await ctx.message.delete()
    tryout_id = str(ctx.message.id)
    
    active_tryouts[tryout_id] = {
        'board_msg_id': None,
        'channel_id': ctx.channel.id,
        'size': size,
        'cooldowns': {},  
        'players': []  
    }
        
    embed, _ = generate_tryout_embed(tryout_id)
    content_text = f"🔥 **{size}V{size} TRYOUT** | Hosted by **{ctx.author.display_name}**\nJoin up to get placed onto a team!"
    board_msg = await ctx.send(content=content_text, embed=embed, view=MainQueueView(tryout_id, is_scrim=False))
    active_tryouts[tryout_id]['board_msg_id'] = board_msg.id


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Denied", delete_after=5)

# Grabs token smoothly using the 'DISCORD_TOKEN' key configured on Railway
token = os.getenv('DISCORD_TOKEN')
if not token:
    raise ValueError("CRITICAL ERROR: 'DISCORD_TOKEN' environment variable is missing from the environment dashboard!")

bot.run(token)