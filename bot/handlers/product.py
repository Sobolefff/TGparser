"""Main flow: a marketplace link in, a product card out."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, Message
from aiogram.utils.chat_action import ChatActionSender

from bot.filters.marketplace import ContainsURLFilter, MarketplaceLinkFilter
from bot.keyboards import product_keyboard
from bot.texts import NO_LINK_TEXT, render_caption
from parsers.exceptions import ParserError, UnsupportedURLError
from services.product_service import ProductCard, ProductService

logger = logging.getLogger(__name__)

router = Router(name="product")


@router.message(F.text | F.caption, MarketplaceLinkFilter())
async def handle_marketplace_link(
    message: Message,
    bot: Bot,
    product_url: str,
    product_service: ProductService,
) -> None:
    """Parse the link and reply with a photo card, keeping the chat action alive meanwhile."""
    async with ChatActionSender.upload_photo(bot=bot, chat_id=message.chat.id):
        try:
            card = await product_service.get_card(product_url)
        except ParserError as exc:
            logger.info("parsing failed for %s: %s", product_url, exc)
            await message.reply(exc.user_message)
            return

    await _send_card(message, card)


@router.message(F.text | F.caption, ContainsURLFilter())
async def handle_unsupported_link(message: Message) -> None:
    """A link that no parser recognises."""
    await message.reply(UnsupportedURLError.user_message)


@router.message(F.text)
async def handle_plain_text(message: Message) -> None:
    """Anything else that is not a link."""
    await message.reply(NO_LINK_TEXT)


async def _send_card(message: Message, card: ProductCard) -> None:
    """Send the card as a photo, falling back to text when Telegram rejects the image."""
    caption = render_caption(card.product)
    keyboard = product_keyboard(card.product)

    if card.image is not None:
        photo = BufferedInputFile(card.image.content, filename=card.image.filename)
        try:
            await message.reply_photo(photo=photo, caption=caption, reply_markup=keyboard)
            return
        except TelegramBadRequest as exc:
            logger.warning("Telegram rejected the photo for %s: %s", card.product.url, exc)

    await message.reply(caption, reply_markup=keyboard)
