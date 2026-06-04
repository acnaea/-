import discord
from discord.ext import commands
from discord.ui import View, Select, Button, Modal, TextInput
import time
import os
from dotenv import load_dotenv

# Load environment variables (Strictly for BOT_TOKEN)
load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- PER-SERVER STORAGE SYSTEM ---
# This structure keeps every server's data completely isolated from one another.
# Key: guild_id (int) -> Value: dict of server-specific data
server_data = {}

def get_server_storage(guild_id: int):
    """Initializes or fetches the isolated memory space for a specific server."""
    if guild_id not in server_data:
        server_data[guild_id] = {
            "allowed_roles": [],    # List of staff role IDs for this server
            "blacklist": set(),     # Set of blacklisted user IDs for this server
            "whitelist": set(),     # Set of whitelisted user IDs for this server
            "active_scrims": {},    # Active scrim matches running on this server
            "active_tryouts": {}    # Active tryout matches running on this server
        }
    return server_data[guild_id]


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

SCRIM_POSITIONS = ["CF", "RW", "LW", "CM", "GK"]


def has_staff_perms(member: discord.Member):
    """Check if member has server admin rights or any of their server's registered staff roles"""
    if member.guild_permissions.administrator:
        return True
    storage = get_server_storage(member.guild.id)
    return any(role.id in storage["allowed_roles"] for role in member.roles)


def format_team_list(team_players, max_size):
    lines = []
    for i in range(max_size):
        if i < len(team_players):
            user_id, style = team_players[i]
            lines.append(f"> **#{i+1}** | <@{user_id}> {style.lower()}")
        else:
            lines.append(f"> **#{i+1}** | ")
    return "\n".join(lines) if lines else "> *Empty*"


def generate_scrim_embed(guild_id, scrim_id):
    storage = get_server_storage(guild_id)
    scrim = storage["active_scrims"].get(scrim_id)
    if not scrim: 
        return None, False
        
    lineup = scrim['lineup']
    description_text = "# SCRIM LINEUP\n"
    
    for pos in SCRIM_POSITIONS:
        if lineup[pos]:
            user_id, style_str = lineup[pos]
            description_text += f"> **{pos:<2}** |  <@{user_id}> {style_str.lower()}\n"
        else:
            description_text += f"> **{pos:<2}** |  \n"
            
    filled_count = sum(1 for val in lineup.values() if val is not None)
    is_full = filled_count >= 5
    
    embed = discord.Embed(description=description_text, color=discord.Color.dark_embed())
    embed.set_footer(text=f"{filled_count}/5 positions filled")
    embed.set_image(url="https://cdn.discordapp.com/attachments/1177079104739213403/1177079546680459354/WHITE.gif")
    return embed, is_full


def generate_tryout_embed(guild_id, tryout_id):
    storage = get_server_storage(guild_id)
    tryout = storage["active_tryouts"].get(tryout_id)
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
    embed.set_image(url="https://cdn.discordapp.com/attachments/1177079104739213403/1177079546680459354/WHITE.gif")
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
        storage = get_server_storage(interaction.guild.id)
        try:
            target_id = int(self.user_input.value.strip())
        except ValueError:
            await interaction.followup.send("❌ Invalid ID format.", ephemeral=True)
            return

        if self.action_type == "blacklist":
            storage['whitelist'].discard(target_id)
            storage['blacklist'].add(target_id)
            
            # Remove player cleanly from this server's active sessions only
            for s_id, scrim in storage['active_scrims'].items():
                for pos, val in scrim['lineup'].items():
                    if val and val[0] == target_id:
                        scrim['lineup'][pos] = None
                        await refresh_main_board(interaction.guild, s_id, is_scrim=True)
            for t_id, tryout in storage['active_tryouts'].items():
                if any(p[0] == target_id for p in tryout['players']):
                    tryout['players'] = [p for p in tryout['players'] if p[0] != target_id]
                    await refresh_main_board(interaction.guild, t_id, is_scrim=False)

            await interaction.followup.send(f"🚫 User `<@{target_id}>` blacklisted on this server.", ephemeral=True)

        elif self.action_type == "whitelist":
            storage['blacklist'].discard(target_id)
            storage['whitelist'].add(target_id)
            await interaction.followup.send(f"✅ User `<@{target_id}>` whitelisted on this server.", ephemeral=True)


class AdminScrimKickDropdown(Select):
    def __init__(self, active_players):
        options = [
            discord.SelectOption(label=p['name'], value=f"{p['is_scrim']}|{p['room_id']}|{p['value']}") 
            for p in active_players
        ]
        super().__init__(placeholder="Select a player to kick...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        storage = get_server_storage(interaction.guild.id)
        
        is_scrim_str, room_id, item_val = self.values[0].split('|')
        is_scrim = is_scrim_str == 'True'
        
        if is_scrim:
            room = storage['active_scrims'].get(room_id)
            if room and item_val in room['lineup']:
                room['lineup'][item_val] = None
        else:
            room = storage['active_tryouts'].get(room_id)
            if room:
                target_id = int(item_val)
                room['players'] = [p for p in room['players'] if p[0] != target_id]
        
        await refresh_main_board(interaction.guild, room_id, is_scrim)
        await interaction.followup.send("👢 Kicked player out of the session.", ephemeral=True)


class AdminControlPanelDashboard(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="👢 Kick Player", style=discord.ButtonStyle.primary, custom_id="admin_kick_btn")
    async def admin_kick(self, interaction: discord.Interaction, button: discord.Button):
        if not has_staff_perms(interaction.user):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return

        storage = get_server_storage(interaction.guild.id)
        active_players = []
        
        for s_id, s_data in storage['active_scrims'].items():
            for pos, val in s_data['lineup'].items():
                if val:
                    member = interaction.guild.get_member(val[0])
                    name = member.display_name if member else f"ID: {val[0]}"
                    active_players.append({'name': f"Scrim: {name} ({pos})", 'value': pos, 'room_id': s_id, 'is_scrim': True})
        
        for t_id, t_data in storage['active_tryouts'].items():
            for p in t_data['players']:
                member = interaction.guild.get_member(p[0])
                name = member.display_name if member else f"ID: {p[0]}"
                active_players.append({'name': f"Tryout: {name}", 'value': str(p[0]), 'room_id': t_id, 'is_scrim': False})

        if not active_players:
            await interaction.response.send_message("ℹ️ No active players found on this server.", ephemeral=True)
            return

        view = View(timeout=60)
        view.add_item(AdminScrimKickDropdown(active_players))
        await interaction.response.send_message("Select player:", view=view, ephemeral=True)

    @discord.ui.button(label="🚫 Blacklist User", style=discord.ButtonStyle.danger, custom_id="admin_blacklist_btn")
    async def admin_blacklist(self, interaction: discord.Interaction, button: discord.Button):
        if not has_staff_perms(interaction.user):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.send_modal(AdminActionModal("blacklist"))

    @discord.ui.button(label="✅ Whitelist User", style=discord.ButtonStyle.success, custom_id="admin_whitelist_btn")
    async def admin_whitelist(self, interaction: discord.Interaction, button: discord.Button):
        if not has_staff_perms(interaction.user):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
        await interaction.response.send_modal(AdminActionModal("whitelist"))


# --- SELECTION & INTERACTION FLOWS ---

async def refresh_main_board(guild, room_id, is_scrim):
    storage = get_server_storage(guild.id)
    room = storage['active_scrims'].get(room_id) if is_scrim else storage['active_tryouts'].get(room_id)
    if not room: return
    try:
        main_channel = guild.get_channel(room['channel_id'])
        board_msg = await main_channel.fetch_message(room['board_msg_id'])
        embed, _ = generate_scrim_embed(guild.id, room_id) if is_scrim else generate_tryout_embed(guild.id, room_id)
        await board_msg.edit(embed=embed)
    except Exception as e:
        print(f"Error syncing board: {e}")


class StyleSelectionDropdown(Select):
    def __init__(self, room_id, is_scrim, position_choice=None):
        self.room_id = room_id
        self.is_scrim = is_scrim
        self.position_choice = position_choice
        super().__init__(placeholder="Pick your play style...", options=[discord.SelectOption(label="Loading...")])

    # Dynamic option initialization on interaction to guarantee up-to-date styles per guild
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        storage = get_server_storage(interaction.guild.id)
        room = storage['active_scrims'].get(self.room_id) if self.is_scrim else storage['active_tryouts'].get(self.room_id)
        
        taken_styles = set()
        if room:
            items = room['lineup'].values() if self.is_scrim else room['players']
            for val in items:
                if val: taken_styles.add(val[1])
        
        available_styles = []
        for option in ALL_PLAYSTYLES:
            if option.label in taken_styles: continue
            if self.is_scrim and self.position_choice == "GK" and option.label == "Glam": continue
            available_styles.append(option)
            
        self.options = available_styles if available_styles else [discord.SelectOption(label="No Styles Available")]
        return True

    async def callback(self, interaction: discord.Interaction):
        storage = get_server_storage(interaction.guild.id)
        room = storage['active_scrims'].get(self.room_id) if self.is_scrim else storage['active_tryouts'].get(self.room_id)
        if not room:
            await interaction.response.send_message("⚠️ Expired.", ephemeral=True)
            return
            
        style_choice = self.values[0]
        if style_choice == "No Styles Available": return

        await interaction.response.defer()
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
                embed, is_full = generate_scrim_embed(guild.id, self.room_id)
                
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
                embed, is_full = generate_tryout_embed(guild.id, self.room_id)
                
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
        super().__init__(placeholder="Pick your position...", options=[discord.SelectOption(label="Loading...")])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        storage = get_server_storage(interaction.guild.id)
        scrim = storage['active_scrims'].get(self.room_id)
        available_options = []
        if scrim:
            for pos_name in SCRIM_POSITIONS:
                if scrim['lineup'][pos_name] is None:
                    available_options.append(discord.SelectOption(label=pos_name))
        self.options = available_options if available_options else [discord.SelectOption(label="Full")]
        return True

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
    print(f"Logged in as {bot.user.name} - Public Per-Server Storage Online")


@bot.event
async def on_interaction(interaction: discord.Interaction):
    if not interaction.data or "custom_id" not in interaction.data: return
        
    custom_id = interaction.data["custom_id"]
    guild = interaction.guild
    user_id = interaction.user.id
    storage = get_server_storage(guild.id)
    
    # --- JOIN SYSTEM ---
    if custom_id.startswith("join_scrim_") or custom_id.startswith("join_tryout_"):
        is_scrim = "scrim_" in custom_id
        room_id = custom_id.replace("join_scrim_", "") if is_scrim else custom_id.replace("join_tryout_", "")
        room = storage['active_scrims'].get(room_id) if is_scrim else storage['active_tryouts'].get(room_id)
        
        if not room:
            await interaction.response.send_message("⚠️ Expired.", ephemeral=True)
            return

        if user_id in storage['blacklist']:
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
            await interaction.response.send_message(content="**Step 1/2** — Pick your position:", view=view, ephemeral=True)
        else:
            if any(p[0] == user_id for p in room['players']):
                await interaction.response.send_message("⚠️ Already joined.", ephemeral=True)
                return
            if len(room['players']) >= (room['size'] * 2):
                await interaction.response.send_message("⚠️ Room full.", ephemeral=True)
                return
                
            view = View(timeout=60)
            view.add_item(StyleSelectionDropdown(room_id, is_scrim=False))
            await interaction.response.send_message(content="Pick your play style to finalize entry:", view=view, ephemeral=True)
        
    # --- LEAVE SYSTEM ---
    elif custom_id.startswith("leave_scrim_") or custom_id.startswith("leave_tryout_"):
        is_scrim = "scrim_" in custom_id
        room_id = custom_id.replace("leave_scrim_", "") if is_scrim else custom_id.replace("leave_tryout_", "")
        room = storage['active_scrims'].get(room_id) if is_scrim else storage['active_tryouts'].get(room_id)
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
        room = storage['active_scrims'].get(room_id) if is_scrim else storage['active_tryouts'].get(room_id)
        
        if not has_staff_perms(interaction.user):
            await interaction.response.send_message("❌ Denied", ephemeral=True)
            return
            
        await interaction.response.defer()
        if room:
            try:
                board_msg = await interaction.channel.fetch_message(room['board_msg_id'])
                await board_msg.edit(content="❌ **CANCELED**", embed=None, view=None)
            except Exception: pass
                
            if is_scrim: storage['active_scrims'].pop(room_id, None)
            else: storage['active_tryouts'].pop(room_id, None)


# --- COMMANDS ---

@bot.command()
@commands.has_permissions(administrator=True)
async def permission(ctx, role: discord.Role = None):
    """View, add, or remove authorized staff roles for this specific server"""
    storage = get_server_storage(ctx.guild.id)
    
    if role is None:
        mentions = " ".join([f"<@&{r_id}>" for r_id in storage["allowed_roles"]])
        await ctx.send(f"ℹ️ Registered staff roles for this server: {mentions if mentions else '`None Set`'}")
        return
        
    if role.id not in storage["allowed_roles"]:
        storage["allowed_roles"].append(role.id)
        await ctx.send(f"✅ Registered {role.mention} as an authorized staff role for this server.")
    else:
        storage["allowed_roles"].remove(role.id)
        await ctx.send(f"🗑️ Removed {role.mention} from this server's staff roles.")


@bot.command()
async def panel(ctx):
    if not has_staff_perms(ctx.author):
        await ctx.send("❌ Denied", delete_after=5)
        return
        
    await ctx.message.delete()
    embed = discord.Embed(title="🛠️ Scrim/Tryout Control Center", color=discord.Color.dark_red())
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
    storage = get_server_storage(ctx.guild.id)
    
    storage['active_scrims'][scrim_id] = {
        'board_msg_id': None,
        'channel_id': ctx.channel.id,
        'region': region_upper,
        'cooldowns': {},  
        'lineup': {pos: None for pos in SCRIM_POSITIONS}
    }
        
    embed, _ = generate_scrim_embed(ctx.guild.id, scrim_id)
    content_text = f"🔹 **{region_upper} SCRIM** | Hosted by **{ctx.author.display_name}**\nPick your position and style to join!"
    board_msg = await ctx.send(content=content_text, embed=embed, view=MainQueueView(scrim_id, is_scrim=True))
    storage['active_scrims'][scrim_id]['board_msg_id'] = board_msg.id


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
    storage = get_server_storage(ctx.guild.id)
    
    storage['active_tryouts'][tryout_id] = {
        'board_msg_id': None,
        'channel_id': ctx.channel.id,
        'size': size,
        'cooldowns': {},  
        'players': []  
    }
        
    embed, _ = generate_tryout_embed(ctx.guild.id, tryout_id)
    content_text = f"**{size}V{size} TRYOUT** | Hosted by **{ctx.author.display_name}**\nJoin up to get placed onto a team!"
    board_msg = await ctx.send(content=content_text, embed=embed, view=MainQueueView(tryout_id, is_scrim=False))
    storage['active_tryouts'][tryout_id]['board_msg_id'] = board_msg.id


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Denied", delete_after=5)

bot.run(os.getenv('BOT_TOKEN'))
