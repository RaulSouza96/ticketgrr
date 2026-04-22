import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import re
import asyncio
import io
from datetime import datetime, UTC

TOKEN = os.getenv("TOKEN")

if not TOKEN:
    raise Exception("TOKEN não encontrado nas variáveis de ambiente")

CONFIG_FILE = "grr_config.json"
DATA_FILE = "grr_data.json"


# =========================
# INTENTS
# =========================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# =========================
# JSON
# =========================
def load_json(path, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=4, ensure_ascii=False)
        return default

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


config_data = load_json(CONFIG_FILE, {
    "ticket_category_id": 0,
    "approval_category_id": 0,
    "approver_role_ids": [],
    "system_locked": False,
    "panel_channel_id": 0,
    "panel_message_id": 0
})

data = load_json(DATA_FILE, {
    "tickets": {},
    "pending_submissions": {},
    "user_actions": {}
})

# trava anti duplicação
processing_tickets = set()


# =========================
# AUXILIARES
# =========================
def dark_blue():
    return discord.Color.dark_blue()


def footer_text():
    return "Raul System"


def sanitize_channel_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^a-zA-Z0-9\-_à-úÀ-Ú]", "", name)
    name = re.sub(r"-{2,}", "-", name)
    if not name:
        name = "usuario"
    return name[:80]


def get_approver_role_ids():
    return config_data.get("approver_role_ids", [])


def is_approver(member: discord.Member) -> bool:
    role_ids = {role.id for role in member.roles}
    return any(role_id in role_ids for role_id in get_approver_role_ids())


def is_system_locked():
    return config_data.get("system_locked", False)


def get_user_stats(user_id: int):
    return data["user_actions"].get(str(user_id), {"total": 0, "actions": {}})


def register_approved_action(user_id: int, action_name: str):
    uid = str(user_id)

    if uid not in data["user_actions"]:
        data["user_actions"][uid] = {
            "total": 0,
            "actions": {}
        }

    data["user_actions"][uid]["total"] += 1
    data["user_actions"][uid]["actions"][action_name] = data["user_actions"][uid]["actions"].get(action_name, 0) + 1
    save_json(DATA_FILE, data)


def add_approver_role(role_id: int):
    roles = config_data.get("approver_role_ids", [])
    if role_id not in roles:
        roles.append(role_id)
        config_data["approver_role_ids"] = roles
        save_json(CONFIG_FILE, config_data)


def remove_approver_role(role_id: int):
    roles = config_data.get("approver_role_ids", [])
    if role_id in roles:
        roles.remove(role_id)
        config_data["approver_role_ids"] = roles
        save_json(CONFIG_FILE, config_data)


# =========================
# EMBEDS
# =========================
def main_panel_embed():
    status = "🔒 Trancado" if is_system_locked() else "🟢 Liberado"

    embed = discord.Embed(
        title="🎫 ticket GRR",
        description=(
            "Clique no botão abaixo para criar seu ticket.\n\n"
            "Dentro do ticket você poderá enviar a ação que participou.\n"
            "O envio do print é obrigatório.\n\n"
            f"**Status do sistema:** {status}"
        ),
        color=dark_blue()
    )
    embed.set_footer(text=footer_text())
    return embed


def ticket_embed(member: discord.Member):
    embed = discord.Embed(
        title="📂 Ticket aberto",
        description=(
            f"Olá {member.mention}, este ticket é privado.\n\n"
            "Use o botão abaixo para:\n"
            "• Enviar ação\n\n"
            "Somente cargos superiores podem fechar este ticket."
        ),
        color=dark_blue()
    )
    embed.set_footer(text=footer_text())
    return embed


def waiting_print_embed(member: discord.Member, action_name: str):
    embed = discord.Embed(
        title="📸 Print obrigatório",
        description=(
            f"{member.mention}, ação registrada: **{action_name}**\n\n"
            "Agora envie **o print neste canal**.\n"
            "Depois disso a solicitação irá para aprovação."
        ),
        color=dark_blue()
    )
    embed.set_footer(text=footer_text())
    return embed


def approval_embed(member: discord.Member, action_name: str):
    embed = discord.Embed(
        title="📨 Solicitação de aprovação",
        description=(
            f"**Usuário:** {member.mention}\n"
            f"**Nome no servidor:** {member.display_name}\n"
            f"**Ação:** `{action_name}`\n"
            f"**ID:** `{member.id}`"
        ),
        color=dark_blue(),
        timestamp=datetime.now(UTC)
    )
    embed.set_footer(text=footer_text())
    return embed


def stats_embed(member: discord.Member):
    stats = get_user_stats(member.id)

    embed = discord.Embed(
        title=f"📊 Ações de {member.display_name}",
        description=f"**Total de ações aprovadas:** {stats['total']}",
        color=dark_blue()
    )

    actions = stats.get("actions", {})
    if actions:
        linhas = []
        for action_name, qtd in sorted(actions.items(), key=lambda x: x[1], reverse=True):
            linhas.append(f"• **{action_name}**: {qtd}")
        embed.add_field(name="Detalhamento", value="\n".join(linhas), inline=False)
    else:
        embed.add_field(name="Detalhamento", value="Nenhuma ação aprovada ainda.", inline=False)

    embed.set_footer(text=footer_text())
    return embed


def config_embed(guild: discord.Guild):
    ticket_cat = guild.get_channel(config_data.get("ticket_category_id", 0))
    approval_cat = guild.get_channel(config_data.get("approval_category_id", 0))

    roles_mentions = []
    for rid in get_approver_role_ids():
        role = guild.get_role(rid)
        if role:
            roles_mentions.append(role.mention)

    embed = discord.Embed(
        title="⚙️ Configuração do ticket GRR",
        color=dark_blue()
    )
    embed.add_field(
        name="Categoria dos tickets",
        value=ticket_cat.mention if ticket_cat else "Não configurada",
        inline=False
    )
    embed.add_field(
        name="Categoria das aprovações",
        value=approval_cat.mention if approval_cat else "Não configurada",
        inline=False
    )
    embed.add_field(
        name="Cargos superiores",
        value=", ".join(roles_mentions) if roles_mentions else "Nenhum configurado",
        inline=False
    )
    embed.add_field(
        name="Status do sistema",
        value="🔒 Trancado" if is_system_locked() else "🟢 Liberado",
        inline=False
    )
    embed.set_footer(text=footer_text())
    return embed


# =========================
# CANAIS / PERMISSÕES
# =========================
async def create_ticket_channel(
    guild: discord.Guild,
    category_id: int,
    channel_name: str,
    owner: discord.Member
):
    category = guild.get_channel(category_id)
    if category is None or not isinstance(category, discord.CategoryChannel):
        return None, "Categoria de ticket inválida ou não configurada."

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        owner: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=not is_system_locked(),
            attach_files=not is_system_locked(),
            read_message_history=True,
            embed_links=True
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            attach_files=True,
            read_message_history=True,
            manage_channels=True,
            manage_messages=True
        )
    }

    for rid in get_approver_role_ids():
        role = guild.get_role(rid)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=not is_system_locked(),
                attach_files=not is_system_locked(),
                read_message_history=True,
                manage_channels=True,
                manage_messages=True
            )

    channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
        topic=f"GRR | dono: {owner} | id: {owner.id}"
    )
    return channel, None


async def create_approval_channel(
    guild: discord.Guild,
    category_id: int,
    channel_name: str
):
    category = guild.get_channel(category_id)
    if category is None or not isinstance(category, discord.CategoryChannel):
        return None, "Categoria de aprovação inválida ou não configurada."

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            attach_files=True,
            read_message_history=True,
            manage_channels=True,
            manage_messages=True
        )
    }

    for rid in get_approver_role_ids():
        role = guild.get_role(rid)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                attach_files=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True
            )

    channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
        topic="GRR | canal privado de aprovação"
    )
    return channel, None


async def lock_ticket_channel(channel: discord.TextChannel, guild: discord.Guild):
    ticket_info = data["tickets"].get(str(channel.id))
    if not ticket_info:
        return

    owner = guild.get_member(ticket_info["owner_id"])
    if owner:
        await channel.set_permissions(
            owner,
            view_channel=True,
            send_messages=False,
            attach_files=False,
            read_message_history=True
        )

    for rid in get_approver_role_ids():
        role = guild.get_role(rid)
        if role:
            await channel.set_permissions(
                role,
                view_channel=True,
                send_messages=False,
                attach_files=False,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True
            )


async def unlock_ticket_channel(channel: discord.TextChannel, guild: discord.Guild):
    ticket_info = data["tickets"].get(str(channel.id))
    if not ticket_info:
        return

    owner = guild.get_member(ticket_info["owner_id"])
    if owner:
        await channel.set_permissions(
            owner,
            view_channel=True,
            send_messages=True,
            attach_files=True,
            read_message_history=True
        )

    for rid in get_approver_role_ids():
        role = guild.get_role(rid)
        if role:
            await channel.set_permissions(
                role,
                view_channel=True,
                send_messages=True,
                attach_files=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True
            )


async def refresh_ticket_control_message(guild: discord.Guild, channel_id: int):
    info = data["tickets"].get(str(channel_id))
    if not info:
        return

    channel = guild.get_channel(int(channel_id))
    if not channel:
        return

    control_message_id = info.get("control_message_id")
    if not control_message_id:
        return

    try:
        msg = await channel.fetch_message(control_message_id)
        owner = guild.get_member(info["owner_id"])
        if owner:
            await msg.edit(embed=ticket_embed(owner), view=TicketControlsView())
    except Exception:
        pass


async def refresh_all_ticket_control_messages(guild: discord.Guild):
    for channel_id in list(data["tickets"].keys()):
        await refresh_ticket_control_message(guild, int(channel_id))


async def refresh_main_panel(guild: discord.Guild):
    channel_id = config_data.get("panel_channel_id", 0)
    message_id = config_data.get("panel_message_id", 0)

    if not channel_id or not message_id:
        return

    channel = guild.get_channel(channel_id)
    if not channel:
        return

    try:
        msg = await channel.fetch_message(message_id)
        await msg.edit(embed=main_panel_embed(), view=MainPanelView())
    except Exception:
        pass


async def set_global_lock(guild: discord.Guild, lock: bool):
    config_data["system_locked"] = lock
    save_json(CONFIG_FILE, config_data)

    for channel_id in list(data["tickets"].keys()):
        channel = guild.get_channel(int(channel_id))
        if channel:
            try:
                if lock:
                    await lock_ticket_channel(channel, guild)
                else:
                    await unlock_ticket_channel(channel, guild)
            except Exception:
                pass

    await refresh_all_ticket_control_messages(guild)
    await refresh_main_panel(guild)


# =========================
# MODAL
# =========================
class ActionModal(discord.ui.Modal, title="Enviar ação"):
    action_name = discord.ui.TextInput(
        label="Nome da ação que participou",
        placeholder="Ex: Jojo, BC, Operação X...",
        required=True,
        max_length=100
    )

    def __init__(self, ticket_channel_id: int, owner_id: int):
        super().__init__()
        self.ticket_channel_id = ticket_channel_id
        self.owner_id = owner_id

    async def on_submit(self, interaction: discord.Interaction):
        if is_system_locked():
            await interaction.response.send_message("O sistema está trancado no momento.", ephemeral=True)
            return

        if interaction.user.id != self.owner_id and not is_approver(interaction.user):
            await interaction.response.send_message("Você não pode usar isso neste ticket.", ephemeral=True)
            return

        data["pending_submissions"][str(self.ticket_channel_id)] = {
            "owner_id": self.owner_id,
            "action_name": str(self.action_name),
            "waiting_print": True,
            "waiting_message_id": 0
        }
        save_json(DATA_FILE, data)

        member = interaction.guild.get_member(self.owner_id)
        await interaction.response.send_message(embed=waiting_print_embed(member, str(self.action_name)))
        try:
            sent = await interaction.original_response()
            data["pending_submissions"][str(self.ticket_channel_id)]["waiting_message_id"] = sent.id
            save_json(DATA_FILE, data)
        except Exception:
            pass


# =========================
# VIEWS
# =========================
class ApprovalView(discord.ui.View):
    def __init__(self, requester_id: int, action_name: str, ticket_channel_id: int):
        super().__init__(timeout=None)
        self.requester_id = requester_id
        self.action_name = action_name
        self.ticket_channel_id = ticket_channel_id

    @discord.ui.button(label="Aprovar", style=discord.ButtonStyle.success, custom_id="grr_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_approver(interaction.user):
            await interaction.response.send_message("Somente cargos superiores podem aprovar.", ephemeral=True)
            return

        register_approved_action(self.requester_id, self.action_name)

        requester = interaction.guild.get_member(self.requester_id)
        ticket_channel = interaction.guild.get_channel(self.ticket_channel_id)

        embed = discord.Embed(
            title="✅ Ação aprovada",
            description=(
                f"**Usuário:** {requester.mention if requester else self.requester_id}\n"
                f"**Ação:** `{self.action_name}`\n"
                f"**Aprovado por:** {interaction.user.mention}"
            ),
            color=dark_blue()
        )
        embed.set_footer(text=footer_text())

        await interaction.response.edit_message(embed=embed, view=None)

        if ticket_channel:
            try:
                aviso = await ticket_channel.send(
                    f"{requester.mention if requester else ''} sua ação **{self.action_name}** foi **APROVADA** por {interaction.user.mention}."
                )
                await asyncio.sleep(5)
                await aviso.delete()
            except Exception:
                pass

        await asyncio.sleep(2)
        try:
            await interaction.channel.delete(reason=f"Ação aprovada por {interaction.user}")
        except Exception:
            pass

    @discord.ui.button(label="Negar", style=discord.ButtonStyle.danger, custom_id="grr_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_approver(interaction.user):
            await interaction.response.send_message("Somente cargos superiores podem negar.", ephemeral=True)
            return

        requester = interaction.guild.get_member(self.requester_id)
        ticket_channel = interaction.guild.get_channel(self.ticket_channel_id)

        embed = discord.Embed(
            title="❌ Ação negada",
            description=(
                f"**Usuário:** {requester.mention if requester else self.requester_id}\n"
                f"**Ação:** `{self.action_name}`\n"
                f"**Negado por:** {interaction.user.mention}"
            ),
            color=dark_blue()
        )
        embed.set_footer(text=footer_text())

        await interaction.response.edit_message(embed=embed, view=None)

        if ticket_channel:
            try:
                aviso = await ticket_channel.send(
                    f"{requester.mention if requester else ''} sua ação **{self.action_name}** foi **NEGADA** por {interaction.user.mention}."
                )
                await asyncio.sleep(5)
                await aviso.delete()
            except Exception:
                pass

        await asyncio.sleep(2)
        try:
            await interaction.channel.delete(reason=f"Ação negada por {interaction.user}")
        except Exception:
            pass


class TicketControlsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id == "grr_send_action":
                item.disabled = is_system_locked()

    @discord.ui.button(label="Enviar Ação", style=discord.ButtonStyle.primary, custom_id="grr_send_action")
    async def send_action(self, interaction: discord.Interaction, button: discord.ui.Button):
        if is_system_locked():
            await interaction.response.send_message("O sistema está trancado no momento.", ephemeral=True)
            return

        ticket_info = data["tickets"].get(str(interaction.channel.id))
        if not ticket_info:
            await interaction.response.send_message("Este canal não está registrado como ticket.", ephemeral=True)
            return

        owner_id = ticket_info["owner_id"]

        if interaction.user.id != owner_id and not is_approver(interaction.user):
            await interaction.response.send_message("Você não pode usar esse botão.", ephemeral=True)
            return

        await interaction.response.send_modal(ActionModal(interaction.channel.id, owner_id))

    @discord.ui.button(label="Fechar Ticket", style=discord.ButtonStyle.danger, custom_id="grr_close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket_info = data["tickets"].get(str(interaction.channel.id))
        if not ticket_info:
            await interaction.response.send_message("Ticket não encontrado.", ephemeral=True)
            return

        if not is_approver(interaction.user):
            await interaction.response.send_message("Somente cargos superiores podem fechar tickets.", ephemeral=True)
            return

        await interaction.response.send_message("🗑️ Ticket será apagado em 3 segundos.")

        cid = str(interaction.channel.id)
        if cid in data["tickets"]:
            del data["tickets"][cid]
        if cid in data["pending_submissions"]:
            del data["pending_submissions"][cid]
        save_json(DATA_FILE, data)

        await asyncio.sleep(3)
        try:
            await interaction.channel.delete(reason=f"Fechado por {interaction.user}")
        except Exception:
            pass


class MainPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if item.custom_id == "grr_create_ticket" and is_system_locked():
                    item.disabled = True
                if item.custom_id == "grr_lock_system" and is_system_locked():
                    item.disabled = True
                if item.custom_id == "grr_unlock_system" and not is_system_locked():
                    item.disabled = True

    @discord.ui.button(label="Criar Ticket", style=discord.ButtonStyle.primary, custom_id="grr_create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if is_system_locked():
            await interaction.response.send_message("O sistema está trancado no momento.", ephemeral=True)
            return

        guild = interaction.guild
        member = interaction.user

        ticket_category_id = config_data.get("ticket_category_id", 0)
        approval_category_id = config_data.get("approval_category_id", 0)
        approver_roles = config_data.get("approver_role_ids", [])

        if not ticket_category_id or not approval_category_id or not approver_roles:
            await interaction.response.send_message(
                "O sistema ainda não foi configurado. Use `/config` primeiro.",
                ephemeral=True
            )
            return

        for channel_id, info in data["tickets"].items():
            if info["owner_id"] == member.id and info["guild_id"] == guild.id:
                existing = guild.get_channel(int(channel_id))
                if existing:
                    await interaction.response.send_message(
                        f"Você já tem um ticket aberto: {existing.mention}",
                        ephemeral=True
                    )
                    return

        channel_name = f"ticket-{sanitize_channel_name(member.display_name)}"
        channel, error = await create_ticket_channel(
            guild=guild,
            category_id=ticket_category_id,
            channel_name=channel_name,
            owner=member
        )

        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        sent = await channel.send(
            content=member.mention,
            embed=ticket_embed(member),
            view=TicketControlsView()
        )

        data["tickets"][str(channel.id)] = {
            "owner_id": member.id,
            "guild_id": guild.id,
            "control_message_id": sent.id,
            "created_at": datetime.now(UTC).isoformat()
        }
        save_json(DATA_FILE, data)

        await interaction.response.send_message(f"✅ Ticket criado: {channel.mention}", ephemeral=True)

    @discord.ui.button(label="Trancar Canal", style=discord.ButtonStyle.secondary, custom_id="grr_lock_system")
    async def lock_system(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (interaction.user.guild_permissions.administrator or is_approver(interaction.user)):
            await interaction.response.send_message("Somente superiores podem trancar o sistema.", ephemeral=True)
            return

        if is_system_locked():
            await interaction.response.send_message("O sistema já está trancado.", ephemeral=True)
            return

        await interaction.response.defer()

        await set_global_lock(interaction.guild, True)

        try:
            await interaction.message.edit(embed=main_panel_embed(), view=MainPanelView())
        except Exception:
            pass

    @discord.ui.button(label="Liberar", style=discord.ButtonStyle.success, custom_id="grr_unlock_system")
    async def unlock_system(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (interaction.user.guild_permissions.administrator or is_approver(interaction.user)):
            await interaction.response.send_message("Somente superiores podem liberar o sistema.", ephemeral=True)
            return

        if not is_system_locked():
            await interaction.response.send_message("O sistema já está liberado.", ephemeral=True)
            return

        await interaction.response.defer()

        await set_global_lock(interaction.guild, False)

        try:
            await interaction.message.edit(embed=main_panel_embed(), view=MainPanelView())
        except Exception:
            pass


# =========================
# EVENTOS
# =========================
@bot.event
async def on_ready():
    try:
        for guild in bot.guilds:
            bot.tree.clear_commands(guild=guild)

        synced = await tree.sync()
        print(f"Comandos globais sincronizados: {len(synced)}")

    except Exception as e:
        print(f"Erro ao sincronizar: {e}")

    bot.add_view(MainPanelView())
    bot.add_view(TicketControlsView())

    await bot.change_presence(activity=discord.CustomActivity(name="ticket GRR"))
    print(f"Bot online como {bot.user}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    pending = data["pending_submissions"].get(str(message.channel.id))
    ticket_info = data["tickets"].get(str(message.channel.id))

    if pending and ticket_info:
        if pending.get("waiting_print") and message.author.id == pending["owner_id"]:
            if is_system_locked():
                await bot.process_commands(message)
                return

            if message.channel.id in processing_tickets:
                await bot.process_commands(message)
                return

            image_attachment = None
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    image_attachment = attachment
                    break

            if image_attachment:
                processing_tickets.add(message.channel.id)

                try:
                    approval_category_id = config_data.get("approval_category_id", 0)
                    member = message.guild.get_member(pending["owner_id"])

                    if not member:
                        await bot.process_commands(message)
                        return

                    action_name = pending["action_name"]
                    waiting_message_id = pending.get("waiting_message_id", 0)

                    if str(message.channel.id) in data["pending_submissions"]:
                        del data["pending_submissions"][str(message.channel.id)]
                        save_json(DATA_FILE, data)

                    approval_channel_name = f"aprov-{sanitize_channel_name(member.display_name)}"
                    approval_channel, error = await create_approval_channel(
                        guild=message.guild,
                        category_id=approval_category_id,
                        channel_name=approval_channel_name
                    )

                    if error:
                        await message.channel.send("Erro ao criar canal de aprovação.")
                        await bot.process_commands(message)
                        return

                    file_bytes = await image_attachment.read()
                    discord_file = discord.File(
                        fp=io.BytesIO(file_bytes),
                        filename=image_attachment.filename
                    )

                    embed = approval_embed(member, action_name)
                    embed.set_image(url=f"attachment://{image_attachment.filename}")

                    await approval_channel.send(
                        embed=embed,
                        file=discord_file,
                        view=ApprovalView(
                            requester_id=member.id,
                            action_name=action_name,
                            ticket_channel_id=message.channel.id
                        )
                    )

                    if waiting_message_id:
                        try:
                            waiting_msg = await message.channel.fetch_message(waiting_message_id)
                            await waiting_msg.delete()
                        except Exception:
                            pass

                    try:
                        await message.delete()
                    except Exception:
                        pass

                    try:
                        aviso = await message.channel.send("📨 Ação enviada para aprovação.")
                        await asyncio.sleep(4)
                        await aviso.delete()
                    except Exception:
                        pass

                finally:
                    processing_tickets.discard(message.channel.id)

    await bot.process_commands(message)


# =========================
# COMANDOS
# =========================
@tree.command(name="config", description="Configura categorias e até 10 cargos superiores")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    categoria_tickets="Categoria onde os tickets serão criados",
    categoria_aprovacoes="Categoria onde os canais de aprovação serão criados",
    cargo_superior_1="Cargo superior 1",
    cargo_superior_2="Cargo superior 2",
    cargo_superior_3="Cargo superior 3",
    cargo_superior_4="Cargo superior 4",
    cargo_superior_5="Cargo superior 5",
    cargo_superior_6="Cargo superior 6",
    cargo_superior_7="Cargo superior 7",
    cargo_superior_8="Cargo superior 8",
    cargo_superior_9="Cargo superior 9",
    cargo_superior_10="Cargo superior 10"
)
async def config(
    interaction: discord.Interaction,
    categoria_tickets: discord.CategoryChannel,
    categoria_aprovacoes: discord.CategoryChannel,
    cargo_superior_1: discord.Role,
    cargo_superior_2: discord.Role = None,
    cargo_superior_3: discord.Role = None,
    cargo_superior_4: discord.Role = None,
    cargo_superior_5: discord.Role = None,
    cargo_superior_6: discord.Role = None,
    cargo_superior_7: discord.Role = None,
    cargo_superior_8: discord.Role = None,
    cargo_superior_9: discord.Role = None,
    cargo_superior_10: discord.Role = None
):
    roles = [
        cargo_superior_1, cargo_superior_2, cargo_superior_3, cargo_superior_4, cargo_superior_5,
        cargo_superior_6, cargo_superior_7, cargo_superior_8, cargo_superior_9, cargo_superior_10
    ]

    unique_roles = []
    seen = set()

    for role in roles:
        if role and role.id not in seen:
            unique_roles.append(role.id)
            seen.add(role.id)

    config_data["ticket_category_id"] = categoria_tickets.id
    config_data["approval_category_id"] = categoria_aprovacoes.id
    config_data["approver_role_ids"] = unique_roles
    save_json(CONFIG_FILE, config_data)

    await interaction.response.send_message(embed=config_embed(interaction.guild), ephemeral=True)


@tree.command(name="config_addcargo", description="Adiciona um cargo superior sem limite")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(cargo="Cargo que será adicionado aos superiores")
async def config_addcargo(interaction: discord.Interaction, cargo: discord.Role):
    if cargo.id in get_approver_role_ids():
        await interaction.response.send_message("Esse cargo já está na lista.", ephemeral=True)
        return

    add_approver_role(cargo.id)
    await interaction.response.send_message(
        f"✅ Cargo {cargo.mention} adicionado com sucesso.",
        ephemeral=True
    )


@tree.command(name="config_removercargo", description="Remove um cargo superior")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(cargo="Cargo que será removido dos superiores")
async def config_removercargo(interaction: discord.Interaction, cargo: discord.Role):
    if cargo.id not in get_approver_role_ids():
        await interaction.response.send_message("Esse cargo não está na lista.", ephemeral=True)
        return

    remove_approver_role(cargo.id)
    await interaction.response.send_message(
        f"✅ Cargo {cargo.mention} removido com sucesso.",
        ephemeral=True
    )


@tree.command(name="config_ver", description="Mostra a configuração atual do sistema")
@app_commands.checks.has_permissions(administrator=True)
async def config_ver(interaction: discord.Interaction):
    await interaction.response.send_message(embed=config_embed(interaction.guild), ephemeral=True)


@tree.command(name="painel_ticket", description="Envia o painel principal do ticket")
@app_commands.checks.has_permissions(administrator=True)
async def painel_ticket(interaction: discord.Interaction):
    if not config_data.get("ticket_category_id") or not config_data.get("approval_category_id") or not config_data.get("approver_role_ids"):
        await interaction.response.send_message(
            "Use `/config` primeiro para configurar categorias e cargos.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        embed=main_panel_embed(),
        view=MainPanelView()
    )

    try:
        sent = await interaction.original_response()
        config_data["panel_channel_id"] = interaction.channel.id
        config_data["panel_message_id"] = sent.id
        save_json(CONFIG_FILE, config_data)
    except Exception:
        pass


@tree.command(name="ver", description="Ver quantas ações aprovadas um usuário possui")
@app_commands.describe(usuario="Usuário que deseja consultar")
async def ver(interaction: discord.Interaction, usuario: discord.Member = None):
    usuario = usuario or interaction.user
    await interaction.response.send_message(embed=stats_embed(usuario), ephemeral=True)


# =========================
# ERROS
# =========================
@config.error
async def config_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        if interaction.response.is_done():
            await interaction.followup.send("Você precisa ser administrador para usar este comando.", ephemeral=True)
        else:
            await interaction.response.send_message("Você precisa ser administrador para usar este comando.", ephemeral=True)
    else:
        if interaction.response.is_done():
            await interaction.followup.send(f"Erro: {error}", ephemeral=True)
        else:
            await interaction.response.send_message(f"Erro: {error}", ephemeral=True)


@config_addcargo.error
async def config_addcargo_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        if interaction.response.is_done():
            await interaction.followup.send("Você precisa ser administrador para usar este comando.", ephemeral=True)
        else:
            await interaction.response.send_message("Você precisa ser administrador para usar este comando.", ephemeral=True)
    else:
        if interaction.response.is_done():
            await interaction.followup.send(f"Erro: {error}", ephemeral=True)
        else:
            await interaction.response.send_message(f"Erro: {error}", ephemeral=True)


@config_removercargo.error
async def config_removercargo_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        if interaction.response.is_done():
            await interaction.followup.send("Você precisa ser administrador para usar este comando.", ephemeral=True)
        else:
            await interaction.response.send_message("Você precisa ser administrador para usar este comando.", ephemeral=True)
    else:
        if interaction.response.is_done():
            await interaction.followup.send(f"Erro: {error}", ephemeral=True)
        else:
            await interaction.response.send_message(f"Erro: {error}", ephemeral=True)


@config_ver.error
async def config_ver_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        if interaction.response.is_done():
            await interaction.followup.send("Você precisa ser administrador para usar este comando.", ephemeral=True)
        else:
            await interaction.response.send_message("Você precisa ser administrador para usar este comando.", ephemeral=True)
    else:
        if interaction.response.is_done():
            await interaction.followup.send(f"Erro: {error}", ephemeral=True)
        else:
            await interaction.response.send_message(f"Erro: {error}", ephemeral=True)


@painel_ticket.error
async def painel_ticket_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        if interaction.response.is_done():
            await interaction.followup.send("Você precisa ser administrador para usar este comando.", ephemeral=True)
        else:
            await interaction.response.send_message("Você precisa ser administrador para usar este comando.", ephemeral=True)
    else:
        if interaction.response.is_done():
            await interaction.followup.send(f"Erro: {error}", ephemeral=True)
        else:
            await interaction.response.send_message(f"Erro: {error}", ephemeral=True)


# =========================
# RUN
# =========================
bot.run(TOKEN)
