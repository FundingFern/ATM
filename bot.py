import os
import sqlite3
import discord
from discord.ext import commands
from datetime import datetime, timezone
from typing import Optional

# --- CONFIG (edit these) ---
LINKTREE_URL = "https://linktr.ee/FundingFern"
CURRENCY = "£"

# Princess Fern user ID
PRINCESS_USER_ID = 1043149535477764146

# ---------------------------

DB_PATH = os.getenv("DB_PATH", "atm_totals.sqlite3")

def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS leak_totals (
                user_id INTEGER PRIMARY KEY,
                total REAL NOT NULL
            )
        """)
        con.commit()

def add_leak_total(user_id: int, amount: float) -> float:
    """Adds amount to user's running total. Returns new total."""
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("SELECT total FROM leak_totals WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        current = float(row[0]) if row else 0.0
        new_total = current + float(amount)

        con.execute(
            "INSERT INTO leak_totals(user_id, total) VALUES(?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET total = excluded.total",
            (user_id, new_total),
        )
        con.commit()
        return new_total


async def safe_edit(interaction: discord.Interaction, **kwargs):
    """
    Safely edits the message whether the interaction has responded already or not.
    """
    try:
        await interaction.response.edit_message(**kwargs)
    except discord.errors.InteractionResponded:
        await interaction.edit_original_response(**kwargs)


intents = discord.Intents.default()
# If you later add role checks / member list features, enable this:
# intents.members = True

bot = commands.Bot(command_prefix="?", intents=intents)


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
        # Princess OR the sub who started /atm can set/update balance
        if not self.session_view.is_allowed(interaction.user):
            return await interaction.response.send_message(
                "This ATM screen isn’t for you.",
                ephemeral=True,
            )

        raw = str(self.amount.value).strip().replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            return await interaction.response.send_message(
                "Please enter a valid number (e.g. 25 or 25.50).",
                ephemeral=True,
            )

        if value < 0:
            return await interaction.response.send_message("Balance can’t be negative.", ephemeral=True)

        self.session_view.balance = round(value, 2)
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
        if not self.session_view.is_allowed(interaction.user):
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
        if not self.session_view.is_allowed(interaction.user):
            await interaction.response.send_message("This withdrawal screen isn’t for you.", ephemeral=True)
            return False
        return True

    def add_amount_button(self, amt: float):
        label = f"{CURRENCY}{amt:,.0f}" if float(amt).is_integer() else f"{CURRENCY}{amt:,.2f}"
        button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)

        async def cb(interaction: discord.Interaction):
            await self.session_view.process_withdraw(interaction, amt)

        button.callback = cb  # type: ignore
        self.add_item(button)

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
        for amt in [10, 15, 20, 25, 30, 50, 75, 100, 200]:
            self.add_amount_button(float(amt))
        return self


class ServiceSelect(discord.ui.Select):
    def __init__(self, session_view: "ATMSessionView"):
        self.session_view = session_view
        options = [
            discord.SelectOption(label="Add balance", value="balance", emoji="🏦"),
            discord.SelectOption(label="Withdraw", value="withdraw", emoji="💸"),
            discord.SelectOption(label="Transaction history", value="history", emoji="🧾"),
            discord.SelectOption(label="Return card", value="return", emoji="💳"),
        ]
        super().__init__(
            placeholder="Select your service…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if not self.session_view.is_allowed(interaction.user):
            return await interaction.response.send_message(
                "This ATM session belongs to someone else. Run **/atm** to open your own.",
                ephemeral=True,
            )

        choice = self.values[0]
        if choice == "balance":
            await self.session_view.show_balance_screen(interaction)
        elif choice == "withdraw":
            wv = WithdrawView(self.session_view).build()
            await self.session_view.push_screen(
                interaction,
                self.session_view.withdraw_text(),
                wv
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
        if not self.session_view.is_allowed(interaction.user):
            await interaction.response.send_message(
                "Access denied. Please hand the card to Princess Fern 👑.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Enter / Update balance", style=discord.ButtonStyle.primary, emoji="✍️")
    async def update_balance(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BalanceModal(self.session_view))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="⬅️")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session_view.render_main(interaction)

    @discord.ui.button(label="Return card", style=discord.ButtonStyle.danger, emoji="🪪")
    async def return_card(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session_view.end_session(interaction)


class HistoryView(discord.ui.View):
    def __init__(self, session_view: "ATMSessionView"):
        super().__init__(timeout=600)
        self.session_view = session_view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not self.session_view.is_allowed(interaction.user):
            await interaction.response.send_message(
                "Access denied. Please hand the card to Princess Fern 👑.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Dispensed", style=discord.ButtonStyle.success, emoji="💳")
    async def dispensed_btn(self, interaction: discord.Interaction, button: discord.ui.Button):

        if self.session_view.dispensed:
            return await interaction.followup.send(
                 "Already marked as dispensed.",
                 ephemeral=True,
            )

        self.session_view.dispensed = True
        button.disabled = True

        # Update the receipt so the button greys out
        await safe_edit(interaction, view=self)

        amt = self.session_view.last_withdrawal
        if amt is None:
            return await interaction.followup.send(
                "No withdrawal amount found for this receipt.",
                ephemeral=True,
            )

        amt_text = (
            f"{CURRENCY}{int(amt):,}" if amt is not None and amt.is_integer()
            else f"{CURRENCY}{amt:,.2f}" if amt is not None
            else "cash"
        )

        user_id = self.session_view.user_id
        total = add_leak_total(user_id, float(amt or 0))
        
        total_text = (
            f"{CURRENCY}{int(total):,}" if float(total).is_integer()
            else f"{CURRENCY}{total:,.2f}"
        )

        msg = (
            f"🏧 **DISPENSED**\n"
            f"ATM toy {self.session_view.user_mention} has successfully leaked "
            f"{amt_text} for Princess Fern 👑 💳 🫦\n\n"
            f"💰 **Total leaked by this toy:** {total_text}"
        )

        # Public announcement (everyone sees it)
        await interaction.channel.send(msg)  # type: ignore

        
        

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
    def __init__(self, princess: discord.Member, user: discord.abc.User):
        super().__init__(timeout=900)

        self.princess_id = princess.id
        self.princess_mention = princess.mention

        self.user_id = user.id
        self.user_mention = user.mention

        self.balance: Optional[float] = None
        self.transactions: list[dict] = []

        self.dispensed = False
        self.last_withdrawal: Optional[float] = None
        self.screen_message: Optional[discord.Message] = None


        self.add_item(ServiceSelect(self))

    def is_allowed(self, user: discord.abc.User) -> bool:
        return user.id in (self.user_id, self.princess_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not self.is_allowed(interaction.user):
            await interaction.response.send_message(
                "Access denied. Please hand the card to Princess Fern 👑.",
                ephemeral=True,
            )
            return False
        return True

    def main_text(self, notice: Optional[str] = None) -> str:
        lines = [f"🏧 Hello {self.user_mention} — you are dispensing for Princess Fern 👑."]
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

    async def render_main(self, interaction: discord.Interaction, notice: Optional[str] = None):
        await self.push_screen(
            interaction,
            self.main_text(notice=notice),
            self
        )
    async def push_screen(self, interaction: discord.Interaction, content: str, view: discord.ui.View):
        # Disable previous ATM screen so old buttons can't be used
        if self.screen_message:
            try:
                await self.screen_message.edit(view=None)
            except Exception:
                pass

        # Send new ATM screen at the bottom of the channel
        await interaction.response.send_message(
        send_kwargs = dict(
 check)
            content=content,
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

        # Save reference to newest screen
        self.screen_message = await interaction.original_response()
        # If interaction already responded, send a followup instead
        if interaction.response.is_done():
            msg = await interaction.followup.send(**send_kwargs, wait=True)
        else:
            await interaction.response.send_message(**send_kwargs)
            msg = await interaction.original_response()

        # Save newest ATM screen
        self.screen_message = msg
 check)

    async def show_balance_screen(self, interaction: discord.Interaction):
        bal = f"{CURRENCY}{self.balance:,.2f}" if self.balance is not None else "Not set"
        content = (
            "🏧 **Balance Inquiry**\n"
            f"Customer: Princess Fern 👑 ({self.princess_mention})\n"
            f"Balance: **{bal}**\n\n"
            "Choose an option below."
        )
        await self.push_screen(
            interaction,
            content,
            BalanceScreenView(self),
        )

    def _receipt_slip(self, withdrawal: float, new_balance: float, tx_id: str) -> str:
        W = 32

        def c(text: str) -> str:
            return text[:W].center(W)

        def lr(left: str, right: str) -> str:
            left = left[:W]
            right = right[:W]
            if len(left) + len(right) + 1 > W:
                left = left[: max(0, W - len(right) - 1)]
            return f"{left}{' ' * (W - len(left) - len(right))}{right}"

        now = datetime.now(timezone.utc)
        ts_line = now.strftime("%d/%m/%Y  %H:%M:%S UTC")
        last4 = str(self.princess_id)[-4:]
        card_line = f"**** **** **** {last4}"
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
            last = self.transactions[-10:]
            lines = ["🏧 **Transaction History**", ""]
            for i, tx in enumerate(reversed(last), 1):
                lines.append(f"**Transaction {i}**")
                lines.append(f"🗓 Date: {tx['ts']} UTC")
                lines.append(f"💸 Type: {tx['type']}")
                lines.append(f"💰 Amount: {tx['amt']}")
                lines.append(f"🏦 Balance Remaining: {tx['bal']}")
                lines.append("────────────────────")
            content = "\n".join(lines)

        await safe_edit(
            interaction,
            content=content,
            view=self,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    async def process_withdraw(self, interaction: discord.Interaction, amount: float):
        if self.balance is None:
            return await self.render_main(
                interaction,
                notice="❌ Balance not set.\nUse **Check balance** to enter / update your balance first.",
            )

        if amount > self.balance:
            return await self.render_main(
                interaction,
                notice=(
                    f"❌ Insufficient funds.\n"
                    f"Requested: **{self._money(amount)}**\n"
                    f"Available: **{self._money(self.balance)}**"
                ),
            )

        self.balance = round(self.balance - amount, 2)

        self.last_withdrawal = amount
        self.dispensed = False
        
        now = datetime.now(timezone.utc)
        tx_id = str(int(now.timestamp()))[-8:]

        self.transactions.append(
            {
                "ts": now.strftime("%Y-%m-%d %H:%M"),
                "type": "WITHDRAW",
                "amt": self._money(amount),
                "bal": self._money(self.balance),
                "id": tx_id,
            }
        )

        slip = self._receipt_slip(withdrawal=amount, new_balance=self.balance, tx_id=tx_id)
        content = f"{slip}\n\n🎁 **Gift here:** {LINKTREE_URL}"

        await safe_edit(
            interaction,
            content=content,
            view=HistoryView(self),
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    async def end_session(self, interaction: discord.Interaction):
        for item in self.children:
            item.disabled = True

        await self.push_screen(
            interaction,
            content=(
                "🏧 **More for Fern, F*cked for Fern, Milked for Fern.**\n\n"
                "Good ATM toy — being used like an object for Princess Fern’s pleasure.\n"
                "Keep slaving away so you can top up my ATM 💳 and I can use you **more, more, more.**\n\n"
                "🙇🏻‍♂️ 💸 🫰🏼 👸🏼"
            ),
            view=self,
           
        embed = discord.Embed(
            title="🏧 FundingFern ATM",
            description=(
                "🏧 **More for Fern, F*cked for Fern, Milked for Fern.**\n\n"
                "Good ATM toy — being used like an object for Princess Fern’s pleasure.\n"
                "Keep slaving away so you can top up my ATM 💳 so I can use you **more, more, more.**\n\n"
                "🙇🏻‍♂️ 💸 🫰🏼 👸🏼"
            ),
            color=0xFF69B4
 check)
        )

        embed.set_image(
            url="https://cdn.discordapp.com/attachments/1438965820993699980/1478807970476327092/ChatGPT_Image_Mar_4_2026_at_05_11_45_PM.png"
        )

        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=None)
        else:
            await interaction.response.send_message(embed=embed)

        self.stop()     

@bot.event
async def on_ready():
    print("BOT VERSION CHECK")
 check)
    try:
        synced = await bot.tree.sync()
        print(f"Ready as {bot.user} | synced {len(synced)} commands")
    except Exception as e:
        print("Command sync failed:", e)


@bot.tree.command(name="atm", description="Start an ATM roleplay session")
async def atm(interaction: discord.Interaction):
    if PRINCESS_USER_ID is None:
        return await interaction.response.send_message("PRINCESS_USER_ID isn’t set yet.", ephemeral=True)

    if not interaction.guild:
        return await interaction.response.send_message("Run this command inside a server.", ephemeral=True)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True)
    except (discord.NotFound, discord.HTTPException):
        return

    if PRINCESS_USER_ID is None:
        return await interaction.followup.send("PRINCESS_USER_ID isn’t set yet.", ephemeral=True)

    if not interaction.guild:
        return await interaction.followup.send("Run this command inside a server.", ephemeral=True)
 check)

    try:
        princess = await interaction.guild.fetch_member(PRINCESS_USER_ID)
    except discord.NotFound:
        return await interaction.response.send_message("Princess Fern isn’t in this server.", ephemeral=True)
    except discord.Forbidden:
        return await interaction.response.send_message(
        return await interaction.followup.send("Princess Fern isn’t in this server.", ephemeral=True)
    except discord.Forbidden:
        return await interaction.followup.send(
 check)
            "I don’t have permission to view server members.", ephemeral=True
        )

    session_view = ATMSessionView(princess, interaction.user)

    embed = discord.Embed(
        title="🏧 FundingFern ATM",
        description=session_view.main_text(),
        color=0xFF69B4
    )

    embed.set_image(
        url="https://cdn.discordapp.com/attachments/1438965820993699980/1478807970476327092/ChatGPT_Image_Mar_4_2026_at_05_11_45_PM.png"
    )

    await interaction.channel.send(f"{princess.mention} 💳 Your ATM toy has started a session.")

    await interaction.followup.send(
        embed=embed,
        view=session_view,
        allowed_mentions=discord.AllowedMentions(users=True),
    )


# Run
TOKEN = os.getenv("BOT2_TOKEN")  # we can rename this to DISCORD_TOKEN when we do Render
if not TOKEN:
    raise RuntimeError("Set BOT2_TOKEN environment variable first.")

init_db()

bot.run(TOKEN)
