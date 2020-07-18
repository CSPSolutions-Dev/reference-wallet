# pyre-ignore-all-errors

# Copyright (c) The Libra Core Contributors
# SPDX-License-Identifier: Apache-2.0

from datetime import datetime
from typing import Optional, List

from sqlalchemy import func, and_, or_

from . import db_session, get_user
from .models import Transaction, TransactionLog
from ..types import TransactionStatus, TransactionType
from libra_utils.types.currencies import LibraCurrency


def add_transaction(
    amount: int,
    currency: LibraCurrency,
    payment_type: TransactionType,
    status: TransactionStatus,
    source_id: int = None,
    source_address: str = None,
    source_subaddress: str = None,
    destination_id: int = None,
    destination_address: str = None,
    destination_subaddress: str = None,
    sequence: Optional[int] = None,
    blockchain_version: Optional[int] = None,
) -> Transaction:
    tx = Transaction(
        amount=amount,
        currency=currency,
        type=payment_type,
        status=status,
        created_timestamp=datetime.utcnow(),
        source_id=source_id,
        source_address=source_address,
        source_subaddress=source_subaddress,
        destination_id=destination_id,
        destination_address=destination_address,
        destination_subaddress=destination_subaddress,
        sequence=sequence,
        blockchain_version=blockchain_version,
    )

    db_session.add(tx)
    db_session.commit()

    return tx


def update_transaction(
    transaction_id: int,
    status: Optional[TransactionStatus] = None,
    blockchain_version: Optional[int] = None,
    sequence: Optional[int] = None,
) -> None:
    tx = Transaction.query.get(transaction_id)
    if status:
        tx.status = status
    if blockchain_version:
        tx.blockchain_version = blockchain_version
    if sequence:
        tx.sequence = sequence

    db_session.add(tx)
    db_session.commit()


def get_transaction(transaction_id: int) -> Transaction:
    return Transaction.query.filter_by(id=transaction_id).first()


def get_transaction_by_blockchain_version(blockchain_version: int) -> Transaction:
    return Transaction.query.filter_by(blockchain_version=blockchain_version).first()


def get_transaction_by_details(
    source_address: str, source_subaddress: Optional[str], sequence: int
):
    return Transaction.query.filter_by(
        source_address=source_address,
        source_subaddress=source_subaddress,
        sequence=sequence,
    ).first()


def get_payment_type(transaction_id):
    return Transaction.query.get(transaction_id).type


def get_transaction_status(transaction_id) -> TransactionStatus:
    tx = Transaction.query.get(transaction_id)
    return tx.status


def save_transaction_log(transaction_id, log) -> None:
    tx = Transaction.query.get(transaction_id)
    tx.logs.append(TransactionLog(log=log, timestamp=datetime.utcnow()))
    db_session.add(tx)
    db_session.commit()


def get_account_transactions(
    account_id: int, currency: Optional[LibraCurrency] = None
) -> List[Transaction]:
    query = Transaction.query.filter(
        or_(
            Transaction.source_id == account_id,
            Transaction.destination_id == account_id,
        ),
    )
    if currency:
        query = query.filter_by(currency=LibraCurrency(currency))

    return query.order_by(Transaction.id.desc()).all()


def get_account_transaction_ids(account_id: int):
    return [tx.id for tx in get_account_transactions(account_id)]


def get_user_transactions(user_id, currency=None):
    user = get_user(user_id=user_id)
    if not user.account_id:
        return None

    query = Transaction.query.filter(
        or_(
            Transaction.source_id == user.account_id,
            Transaction.destination_id == user.account_id,
        )
    )

    if currency:
        query = query.filter_by(currency=LibraCurrency(currency))

    return query.order_by(Transaction.id.desc()).all()


def get_single_transaction(transaction_id: int):
    tx = Transaction.query.get(transaction_id)
    db_session.refresh(tx)
    return tx


def get_transaction_logs(transaction_id):
    return Transaction.query.get(transaction_id).logs


def get_transaction_amount(transaction_id):
    return Transaction.query.get(transaction_id).amount


def get_total_currency_credits():
    return (
        Transaction.query.with_entities(
            Transaction.currency,
            Transaction.status,
            func.sum(Transaction.amount).label("amount"),
        )
        .filter(
            and_(
                Transaction.type == TransactionType.EXTERNAL,
                Transaction.destination_id.isnot(None),
            )
        )
        .group_by(Transaction.currency, Transaction.status,)
        .all()
    )


def get_total_currency_debits():
    return (
        Transaction.query.with_entities(
            Transaction.currency,
            Transaction.status,
            func.sum(Transaction.amount).label("amount"),
        )
        .filter(
            and_(
                Transaction.type == TransactionType.EXTERNAL,
                Transaction.source_id.isnot(None),
            )
        )
        .group_by(Transaction.currency, Transaction.status,)
        .all()
    )