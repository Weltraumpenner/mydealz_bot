import logging
from typing import Any, Dict, Optional, Union

from aiogram.dispatcher import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, ReplyKeyboardRemove, Update
from aiogram.utils.exceptions import MessageCantBeEdited

from src.db.constants import UColumns
from src.db.tables import SQLiteNotifications, SQLiteUser
from src.exceptions import NotificationNotFoundError, UserNotFoundError
from src.models import NotificationModel, UserModel
from src.telegram import keyboards, messages
from src.telegram.constants import Commands, States

CallbackDataType = Dict[str, Any]


# pylint: disable=too-many-public-methods
class Handlers:
    @classmethod
    async def start(cls, message: Message) -> None:
        sqlite_user = SQLiteUser()
        try:
            user = await cls.__get_user(message)
        except UserNotFoundError:
            user = UserModel().parse_telegram_chat(message.chat)
            logging.info('added user: %s', user.__dict__)
            await sqlite_user.upsert_model(user)

        notifications = await SQLiteNotifications().get_by_user_id(user.id)

        await cls.__overwrite_or_answer(message, messages.start(user), keyboards.start(notifications))

    @classmethod
    async def help(cls, message: Message) -> None:
        await cls.__overwrite_or_answer(message, messages.help_msg())

    @classmethod
    async def start_over(cls, telegram_object: Union[Message, CallbackQuery], state: FSMContext) -> None:
        await cls.__finish_state(state)
        await cls.start(telegram_object.message if isinstance(telegram_object, CallbackQuery) else telegram_object)

    @classmethod
    async def settings(cls, telegram_object: Union[Message, CallbackQuery], state: Optional[FSMContext] = None) -> None:
        await cls.__finish_state(state)
        user = await cls.__get_user(telegram_object)

        await cls.__overwrite_or_answer(
            telegram_object.message if isinstance(telegram_object, CallbackQuery) else telegram_object,
            messages.settings(user),
            reply_markup=keyboards.settings(user)
        )

    @classmethod
    async def add_notification(cls, query: CallbackQuery) -> None:
        await States.ADD_NOTIFICATION.set()
        await cls.__overwrite_or_answer(query.message, messages.query_instructions())

    @classmethod
    async def process_add_notification(cls, message: Message, state: FSMContext) -> None:
        notification = await cls.__save_notification(message.text, message.chat.id)
        await state.finish()
        await cls.__overwrite_or_answer(
            message,
            messages.notification_added(notification),
            reply_markup=keyboards.notification_commands(notification)
        )

    @classmethod
    async def show_notification(cls, query: CallbackQuery, callback_data: Dict[str, Any]) -> None:
        notification = await cls.__get_notification(callback_data)
        await cls.__overwrite_or_answer(
            query.message,
            messages.notification_overview(notification),
            reply_markup=keyboards.notification_commands(notification)
        )

    @classmethod
    async def edit_query(cls, query: CallbackQuery, callback_data: Dict[str, Any], state: FSMContext) -> None:
        await cls.__store_notification_id(state, callback_data)
        await States.EDIT_QUERY.set()
        await cls.__overwrite_or_answer(query.message, messages.query_instructions())

    @classmethod
    async def process_edit_query(cls, message: Message, state: FSMContext) -> None:
        notification = await cls.__get_notification(state)
        notification.query = message.text
        await SQLiteNotifications().upsert_model(notification)

        await cls.__overwrite_or_answer(
            message,
            messages.query_updated(notification),
            reply_markup=keyboards.notification_commands(notification)
        )

    @classmethod
    async def edit_min_price(
            cls, query: CallbackQuery, callback_data: Dict[str, Any], state: FSMContext
    ) -> None:
        await cls.__store_notification_id(state, callback_data)
        await States.EDIT_MIN_PRICE.set()
        await cls.__overwrite_or_answer(query.message, messages.price_instructions('Min'))

    @classmethod
    async def process_edit_min_price(cls, message: Message, state: FSMContext) -> None:
        price = message.text
        if price == f'/{Commands.REMOVE}':
            price = '0'

        notification = await cls.__get_notification(state)

        notification.min_price = round(float(price.replace(',', '.')))
        await SQLiteNotifications().upsert_model(notification)

        await cls.__overwrite_or_answer(
            message,
            messages.query_updated(notification),
            keyboards.notification_commands(notification)
        )

        await state.finish()

    @classmethod
    async def edit_max_price(
            cls, query: CallbackQuery, callback_data: Dict[str, Any], state: FSMContext
    ) -> None:
        await cls.__store_notification_id(state, callback_data)
        await States.EDIT_MAX_PRICE.set()
        await cls.__overwrite_or_answer(query.message, messages.price_instructions('Max'))

    @classmethod
    async def process_edit_max_price(cls, message: Message, state: FSMContext) -> None:
        price = message.text
        if price == f'/{Commands.REMOVE}':
            price = '0'

        notification = await cls.__get_notification(state)
        notification.max_price = round(float(price.replace(',', '.')))
        await SQLiteNotifications().upsert_model(notification)

        await cls.__overwrite_or_answer(
            message,
            messages.query_updated(notification),
            keyboards.notification_commands(notification)
        )

        await state.finish()

    @classmethod
    async def toggle_only_hot(cls, query: CallbackQuery, callback_data: Dict[str, Any]) -> None:
        notification = await cls.__get_notification(callback_data)
        notification.search_only_hot = not notification.search_only_hot
        await SQLiteNotifications().upsert_model(notification)

        await cls.show_notification(query, callback_data)

    @classmethod
    async def toggle_mydealz(cls, query: CallbackQuery) -> None:
        await SQLiteUser().toggle_field(cls.__get_user_id(query), UColumns.SEARCH_MYDEALZ)
        await cls.settings(query)

    @classmethod
    async def toggle_mindstar(cls, query: CallbackQuery) -> None:
        await SQLiteUser().toggle_field(cls.__get_user_id(query), UColumns.SEARCH_MINDSTAR)
        await cls.settings(query)

    @classmethod
    async def toggle_preisjaeger(cls, query: CallbackQuery) -> None:
        await SQLiteUser().toggle_field(cls.__get_user_id(query), UColumns.SEARCH_PREISJAEGER)
        await cls.settings(query)

    @classmethod
    async def delete_notification(cls, query: CallbackQuery, callback_data: Dict[str, Any]) -> None:
        notification = await cls.__get_notification(callback_data)
        await SQLiteNotifications().delete_by_id(notification.id)

        await cls.__overwrite_or_answer(query.message, messages.notification_deleted(notification))

    @classmethod
    async def add_notification_inconclusive(cls, message: Message) -> None:
        text = message.text
        await cls.__overwrite_or_answer(
            message,
            messages.add_notification_inconclusive(text),
            reply_markup=keyboards.add_notification_inconclusive(text)
        )

    @classmethod
    async def process_add_notification_inconclusive(cls, query: CallbackQuery, callback_data: Dict[str, Any]) -> None:
        notification = await cls.__save_notification(callback_data.get('query', ''), query.message.chat.id)

        await cls.__overwrite_or_answer(
            query.message,
            messages.notification_added(notification),
            reply_markup=keyboards.notification_commands(notification)
        )

    @classmethod
    async def cancel(cls, message: Message, state: FSMContext) -> None:
        if not await state.get_state():
            return
        try:
            notification = await cls.__get_notification(state)

        except NotificationNotFoundError:
            await cls.start_over(message, state)
        else:
            await cls.__overwrite_or_answer(
                message,
                messages.notification_overview(notification),
                reply_markup=keyboards.notification_commands(notification)
            )

        await cls.__finish_state(state)

    @classmethod
    async def invalid_query(cls, message: Message) -> None:
        await cls.__overwrite_or_answer(message, messages.invalid_query())

    @classmethod
    async def invalid_price(cls, message: Message, state: FSMContext) -> None:
        price_type = 'Min' if await state.get_state() == States.EDIT_MIN_PRICE else 'Max'

        await cls.__overwrite_or_answer(message, messages.invalid_price(price_type))

    @classmethod
    async def notification_not_found(cls, update: Update, error: Exception) -> bool:  # pylint: disable=unused-argument
        if update.callback_query:
            await update.callback_query.message.edit_text(messages.notification_not_found(), reply_markup=None)
        else:
            await update.message.answer(messages.notification_not_found(), reply_markup=None)

        return True

    @classmethod
    async def __overwrite_or_answer(
            cls, message: Message, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None
    ) -> None:
        try:
            await message.edit_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
        except MessageCantBeEdited:
            await message.answer(
                text, reply_markup=reply_markup or ReplyKeyboardRemove(), disable_web_page_preview=True
            )

    @classmethod
    async def __save_notification(cls, query: str, chat_id: int) -> NotificationModel:
        notification = NotificationModel()
        notification.user_id = chat_id
        notification.query = query
        notification.id = await SQLiteNotifications().upsert_model(notification)

        logging.info('user %s added notification %s (%s)', notification.user_id, notification.id, notification.query)

        return notification

    @classmethod
    async def __store_notification_id(cls, state: FSMContext, callback_data: CallbackDataType) -> None:
        async with state.proxy() as data:
            data['notification_id'] = callback_data['notification_id']

    @classmethod
    async def __get_notification(cls, source: Union[CallbackDataType, FSMContext]) -> NotificationModel:
        if isinstance(source, FSMContext):
            async with source.proxy() as data:
                notification_id = data.get('notification_id', 0)
        else:
            notification_id = source.get('notification_id', 0)

        notification = await SQLiteNotifications().get_by_id(notification_id)
        if not notification:
            raise NotificationNotFoundError(notification_id)

        return notification

    @classmethod
    def __get_user_id(cls, source: Union[Message, CallbackQuery]) -> int:
        if isinstance(source, Message):
            return int(source.chat.id)

        return int(source.message.chat.id)

    @classmethod
    async def __get_user(cls, source: Union[Message, CallbackQuery]) -> UserModel:
        user_id = cls.__get_user_id(source)
        user = await SQLiteUser().get_by_id(user_id)

        if not user:
            raise UserNotFoundError(user_id)

        return user

    @classmethod
    async def __finish_state(cls, state: Optional[FSMContext]) -> None:
        if state and await state.get_state():
            await state.finish()
