import os
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone

async def safe_edit(interaction: discord.Interaction, **kwargs):
    try:
        await interaction.response.edit_message(**kwargs)
    except discord.errors.InteractionResponded:
        await interaction.edit_original_response(**kwargs)

async def safe_edit(interaction: discord.Interaction, **kwargs):
    try:
        await interaction.response.edit_message(**kwargs)
    except discord.errors.InteractionResponded:
        await interaction.edit_original_response(**kwargs)

# --- CONFIG (edit these) ---
LINKTREE_URL = "https://linktr.ee/FundingFern"
CURRENCY = "£"                                # change to "$" etc if you want

# If you want only you (Princess) to be selectable, set this to your Discord user ID (int).
# Example: PRINCESS_USER_ID = 123456789012345678
# If None, the starter screen lets the user select ANY member, like you requested.
PRINCESS_USER_ID = 1043149535477764146
# ---------------------------


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="?", intents=intents)


def is_princess(member: discord.abc.User) -> bool:
    return PRINCESS_USER_ID is not None and getattr(member, "id", None) == PRINCESS_USER_ID


class BalanceModal(discord.ui.Modal, title="Enter balance amount"):
    amount = discord.ui.TextInput(
        label="Balance",
        placeholder="e.g. 250",
        required=True,
        max_length=12,
    )

    def __init__(self, session_view: "ATMSessionView"):
        super().__init__()
        self.session_view = session_view

    async def on_submit(self, interaction: discord.Interaction):
        # only the selected "Princess" can submit
        if interaction.user.id != self.session_view.princess_id:
            return await interaction.response.send_message("This ATM screen isn’t for you.", ephemeral=True)

        raw = str(self.amount.value).strip().replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            return await interaction.response.send_message("Please enter a valid number (e.g. 25 or 25.50).", ephemeral=True)

        if value < 0:
            return await interaction.response.send_message("Balance can’t be negative.", ephemeral=True)

        self.session_view.balance = value
        await self.session_view.render_main(interaction, notice=f"Balance set to {CURRENCY}{value:,.2f}")


class OtherWithdrawModal(discord.ui.Modal, title="Other withdrawal amount"):
    amount = discord.ui.TextInput(
        label="Amount",
        placeholder="e.g. 15",
        required=True,
        max_length=12,
    )

    def __init__(self, session_view: "ATMSessionView"):
        super().__init__()
        self.session_view = session_view

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.session_view.princess_id:
            return await interaction.response.send_message("This ATM screen isn’t for you.", ephemeral=True)

        raw = str(self.amount.value).strip().replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            return await interaction.response.send_message("Please enter a valid number.", ephemeral=True)

        if value <= 0:
            return await interaction.response.send_message("Amount must be greater than 0.", ephemeral=True)

        await self.session_view.process_withdraw(interaction, value)


class WithdrawView(discord.ui.View):
    def __init__(self, session_view: "ATMSessionView"):
        super().__init__(timeout=600)
        self.session_view = session_view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.session_view.princess_id:
            await interaction.response.send_message("This withdrawal screen isn’t for you.", ephemeral=True)
            return False
        return True

    def add_amount_button(self, amt: float):
        label = f"{CURRENCY}{amt:,.0f}" if amt.is_integer() else f"{CURRENCY}{amt:,.2f}"

        async def cb(interaction: discord.Interaction):
            await self.session_view.process_withdraw(interaction, amt)

        self.add_item(discord.ui.Button(label=label, style=discord.ButtonStyle.primary, custom_id=f"w_{amt}"))
        self.children[-1].callback = cb  # type: ignore

    @discord.ui.button(label="Other amount", style=discord.ButtonStyle.secondary)
    async def other_amount(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(OtherWithdrawModal(self.session_view))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session_view.render_main(interaction)

    @discord.ui.button(label="Return card", style=discord.ButtonStyle.danger)
    async def return_card(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session_view.end_session(interaction)

    def build(self):
        # set amounts you requested
        for amt in [10, 15, 20, 25, 30, 50, 75, 100, 200]:
            self.add_amount_button(float(amt))
        return self


class ServiceSelect(discord.ui.Select):
    def __init__(self, session_view: "ATMSessionView"):
        self.session_view = session_view
        options = [
            discord.SelectOption(label="Check balance", value="balance", emoji="💳"),
            discord.SelectOption(label="Withdraw", value="withdraw", emoji="💸"),
            discord.SelectOption(label="Transaction history", value="history", emoji="🧾"),
            discord.SelectOption(label="Return card", value="return", emoji="🪪"),
            
        ]
        super().__init__(
            placeholder="Select your service…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.session_view.princess_id:
            return await interaction.response.send_message("This ATM screen isn’t for you.", ephemeral=True)

        choice = self.values[0]
        if choice == "balance":
            await self.session_view.show_balance_screen(interaction)
        elif choice == "withdraw":
            wv = WithdrawView(self.session_view).build()
            await interaction.response.edit_message(
                content=self.session_view.withdraw_text(),
                view=wv,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        elif choice == "history":
            await self.session_view.show_history_screen(interaction)
        else:
            await self.session_view.end_session(interaction)

class BalanceScreenView(discord.ui.View):
    def __init__(self, session_view: "ATMSessionView"):
        super().__init__(timeout=600)
        self.session_view = session_view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.session_view.princess_id:
            await interaction.response.send_message(
                "Access denied. Please hand the card to Princess Fern 👑.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Enter / Update balance", style=discord.ButtonStyle.primary, emoji="✍️")
    async def update_balance(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BalanceModal(self.session_view))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="⬅️")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        # go back to the main “select your service” screen
        await interaction.response.edit_message(
            content=self.session_view.main_text(),
            view=self.session_view,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @discord.ui.button(label="Return card", style=discord.ButtonStyle.danger, emoji="🪪")
    async def return_card(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session_view.end_session(interaction)

class ReceiptView(discord.ui.View):
    def __init__(self, session_view: "ATMSessionView"):
        super().__init__(timeout=600)
        self.session_view = session_view

class HistoryView(discord.ui.View):
    def __init__(self, session_view: "ATMSessionView"):
        super().__init__(timeout=600)
        self.session_view = session_view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.session_view.princess_id:
            await interaction.response.send_message(
                "Access denied. Please hand the card to Princess Fern 👑.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Back to services", style=discord.ButtonStyle.primary, emoji="🏧")
    async def back_services(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=self.session_view.main_text(),
            view=self.session_view,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @discord.ui.button(label="Withdraw again", style=discord.ButtonStyle.secondary, emoji="💸")
    async def withdraw_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        wv = WithdrawView(self.session_view).build()
        await interaction.response.edit_message(
            content=self.session_view.withdraw_text(),
            view=wv,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @discord.ui.button(label="Return card", style=discord.ButtonStyle.danger, emoji="🪪")
    async def return_card(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session_view.end_session(interaction)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.session_view.princess_id:
            await interaction.response.send_message(
                "Access denied. Please hand the card to Princess Fern 👑.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Back to services", style=discord.ButtonStyle.primary, emoji="🏧")
    async def back_services(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=self.session_view.main_text(),
            view=self.session_view,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @discord.ui.button(label="Withdraw again", style=discord.ButtonStyle.secondary, emoji="💸")
    async def withdraw_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        wv = WithdrawView(self.session_view).build()
        await interaction.response.edit_message(
            content=self.session_view.withdraw_text(),
            view=wv,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @discord.ui.button(label="Return card", style=discord.ButtonStyle.danger, emoji="🪪")
    async def return_card(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session_view.end_session(interaction)
class ReceiptView(discord.ui.View):
    def __init__(self, session_view: "ATMSessionView"):
        super().__init__(timeout=600)
        self.session_view = session_view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.session_view.princess_id:
            await interaction.response.send_message(
                "Access denied. Please hand the card to Princess Fern 👑.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Back to services", style=discord.ButtonStyle.primary, emoji="🏧")
    async def back_services(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=self.session_view.main_text(),
            view=self.session_view,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @discord.ui.button(label="Withdraw again", style=discord.ButtonStyle.secondary, emoji="💸")
    async def withdraw_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        wv = WithdrawView(self.session_view).build()
        await interaction.response.edit_message(
            content=self.session_view.withdraw_text(),
            view=wv,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @discord.ui.button(label="Return card", style=discord.ButtonStyle.danger, emoji="🪪")
    async def return_card(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session_view.end_session(interaction)

class ATMSessionView(discord.ui.View):
    def __init__(self, princess: discord.Member):
        super().__init__(timeout=900)
        self.princess_id = princess.id
        self.princess_mention = princess.mention
        self.balance: float | None = None
        self.transactions = []
        self.add_item(ServiceSelect(self))

    async def show_balance_screen(self, interaction: discord.Interaction):
        bal = f"{CURRENCY}{self.balance:,.2f}" if self.balance is not None else "Not set"
        content = (
            "🏧 **Balance Inquiry**\n"
            f"Customer: Princess Fern 👑 ({self.princess_mention})\n"
            f"Balance: **{bal}**\n\n"
            "Choose an option below."
        )
        await interaction.response.edit_message(
            content=content,
            view=BalanceScreenView(self),
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.princess_id:
            await interaction.response.send_message(
                "Access denied. Please hand the card to Princess Fern 👑.",
                ephemeral=True
            )
            return False
        return True

    def main_text(self, notice: str | None = None) -> str:
        lines = [
            f"🏧 Hello Princess Fern 👑 ({self.princess_mention}), please select your service today."
        ]

        if self.balance is not None:
            lines.append(f"**Current balance:** {CURRENCY}{self.balance:,.2f}")

        if notice:
            lines.append(f"\n{notice}")

        return "\n".join(lines)

    def withdraw_text(self) -> str:
        return (
            f"🏧 Princess Fern 👑 ({self.princess_mention}) — Withdrawal menu\n"
            f"Choose an amount below, or pick **Other amount**."
        )

    def _money(self, amount: float) -> str:
        return f"{CURRENCY}{amount:,.2f}"

    def _receipt_slip(self, withdrawal: float, new_balance: float, tx_id: str) -> str:
        # 32-char width is a sweet spot for Discord mobile + “thermal” feel
        W = 32

        def c(text: str) -> str:
            return text[:W].center(W)

        def lr(left: str, right: str) -> str:
            left = left[:W]
            right = right[:W]
            if len(left) + len(right) + 1 > W:
                left = left[: max(0, W - len(right) - 1)]
            return f"{left}{' ' * (W - len(left) - len(right))}{right}"

        # realistic-ish printed details (roleplay)
        now = datetime.now(timezone.utc)
        ts_line = now.strftime("%d/%m/%Y  %H:%M:%S UTC")

        # masked “card” (stable-ish but fake)
        last4 = str(self.princess_id)[-4:]
        card_line = f"**** **** **** {last4}"

        # make simple pseudo codes from tx_id
        auth = f"A{tx_id[-6:]}"
        term = f"T{tx_id[:6]}"

        amt = self._money(withdrawal)
        bal = self._money(new_balance)

        lines = [
            c("FUNDINGFERN BANK"),
            c("MORE FOR FERN, F*CKED FOR FERN"),
            "-" * W,
            c("WITHDRAWAL RECEIPT"),
            "-" * W,
            lr("DATE/TIME", ts_line),
            lr("TERMINAL", term),
            lr("AUTH CODE", auth),
            lr("TXN ID", tx_id),
            "-" * W,
            lr("CARD", card_line),
            "-" * W,
            lr("WITHDRAWAL", amt),
            lr("AVAILABLE BAL", bal),
            "-" * W,
            c("DISPENSE GIFT"),
            c("LINK BELOW"),
            "-" * W,
            c("SAY THANK YOU PRINCESS 👑"),
        ]

        return "```text\n" + "\n".join(lines) + "\n```"

    async def show_history_screen(self, interaction: discord.Interaction):
        if not self.transactions:
            content = (
                "🏧 **Transaction History**\n\n"
                "No transactions yet.\n"
                "Make a withdrawal to generate your first receipt."
            )
        else:
            last = self.transactions[-10:]  # last 10 only

            lines = ["🏧 **Transaction History**", ""]

            for i, tx in enumerate(reversed(last), 1):
                lines.append(f"**Transaction {i}**")
                lines.append(f"🗓 Date: {tx['ts']} UTC")
                lines.append(f"💸 Type: {tx['type']}")
                lines.append(f"💰 Amount: {tx['amt']}")
                lines.append(f"🏦 Balance Remaining: {tx['bal']}")
                lines.append("────────────────────")
            
            content = "\n".join(lines)

        try:
            await safe_edit(
            interaction,
            content=content,
            view=self,
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        except discord.errors.InteractionResponded:
            await interaction.edit_original_response(
                content=content,
                view=self,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        await interaction.edit_original_response(
            content=content,
            view=self,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    async def render_main(self, interaction: discord.Interaction, notice: str | None = None):
        await interaction.response.edit_message(
            content=self.main_text(notice=notice),
            view=self,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    async def process_withdraw(self, interaction: discord.Interaction, amount: float):
        # Ensure a balance exists
        if self.balance is None:
            notice = (
                "❌ Balance not set.\n"
                "Use **Check balance** to enter / update your balance first."
            )
            return await self.render_main(interaction, notice=notice)

        # Insufficient funds
        if amount > self.balance:
            notice = (
                f"❌ Insufficient funds.\n"
                f"Requested: **{self._money(amount)}**\n"
                f"Available: **{self._money(self.balance)}**"
            )
            return await self.render_main(interaction, notice=notice)

        # Decrease balance
        self.balance = round(self.balance - amount, 2)

        # Create transaction ID + log it
        now = datetime.now(timezone.utc)
        tx_id = str(int(now.timestamp()))[-8:]

        self.transactions.append({
            "ts": now.strftime("%Y-%m-%d %H:%M"),
            "type": "WITHDRAW",
            "amt": self._money(amount),
            "bal": self._money(self.balance),
            "id": tx_id,
        })

        # Build receipt slip
        slip = self._receipt_slip(
            withdrawal=amount,
            new_balance=self.balance,
            tx_id=tx_id
        )

        # Add clickable gift link OUTSIDE the code block
        content = f"{slip}\n\n🎁 **Gift here:** {LINKTREE_URL}"

        # Replace the same ATM message with receipt screen
        await interaction.response.edit_message(
            content=content,
            view=HistoryView(self),
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    async def end_session(self, interaction: discord.Interaction):
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content="✅ Thank you for using me, Princess Fern 👑.",
            view=self,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

        self.stop()



@bot.event
async def on_ready():
    # sync slash commands
    try:
        synced = await bot.tree.sync()
        print(f"Ready as {bot.user} | synced {len(synced)} commands")
    except Exception as e:
        print("Command sync failed:", e)


@bot.tree.command(name="atm", description="Start an ATM roleplay session")
async def atm(interaction: discord.Interaction):

    if PRINCESS_USER_ID is None:
        return await interaction.response.send_message(
            "PRINCESS_USER_ID isn’t set yet.",
            ephemeral=True
        )

    if not interaction.guild:
        return await interaction.response.send_message(
            "Run this command inside a server.",
            ephemeral=True
        )

    try:
        princess = await interaction.guild.fetch_member(PRINCESS_USER_ID)
    except discord.NotFound:
        return await interaction.response.send_message(
            "Princess Fern isn’t in this server.",
            ephemeral=True
        )
    except discord.Forbidden:
        return await interaction.response.send_message(
            "I don’t have permission to view server members.",
            ephemeral=True
        )

    session_view = ATMSessionView(princess)

    await interaction.response.send_message(
        content=session_view.main_text(),
        view=session_view,
        allowed_mentions=discord.AllowedMentions(users=True),
        ephemeral=False,
    )


# Run
TOKEN = os.getenv("BOT2_TOKEN")
if not TOKEN:
    raise RuntimeError("Set BOT2_TOKEN environment variable first.")
bot.run(TOKEN)