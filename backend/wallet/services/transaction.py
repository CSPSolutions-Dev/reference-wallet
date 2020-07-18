# Copyright (c) The Libra Core Contributors
# SPDX-License-Identifier: Apache-2.0

from time import sleep
from typing import Optional

from pylibra import LibraNetwork, AccountResource

from libra_utils.libra import decode_subaddr
from libra_utils.types.currencies import LibraCurrency
from pubsub.types import TransactionMetadata
from wallet.services import account as account_service
from wallet.services.risk import risk_check, TX_AMOUNT_THRESHOLD
from . import INVENTORY_ACCOUNT_NAME
from .log import add_transaction_log
from .. import storage, services, OnchainWallet
from ..logging import log_execution
from ..storage import (
    add_transaction,
    Transaction,
    get_transaction_by_details,
    get_total_currency_credits,
    get_total_currency_debits,
)
from ..storage import get_account_id_from_subaddr, get_account
from ..types import (
    TransactionDirection,
    TransactionType,
    TransactionStatus,
    BalanceError,
    Balance,
)


class RiskCheckError(Exception):
    pass


class SelfAsDestinationError(Exception):
    pass


def process_incoming_transaction(
    blockchain_version: int,
    sender_address: str,
    receiver_address: str,
    sequence: int,
    amount: int,
    currency: LibraCurrency,
    metadata: Optional[TransactionMetadata] = None,
):
    log_execution("Attempting to process incoming transaction from chain")
    receiver_id = None
    sender_subaddress = None
    receiver_subaddr = None
    if metadata:
        if metadata.to_subaddress:
            receiver_subaddr = decode_subaddr(metadata.to_subaddress)
            receiver_id = get_account_id_from_subaddr(receiver_subaddr)

        sender_subaddress = decode_subaddr(metadata.from_subaddress)
        if sender_subaddress == "":
            sender_subaddress = None

    if not receiver_id:
        log_execution("Incoming transaction had no metadata. crediting inventory")
        receiver_id = get_account(account_name=INVENTORY_ACCOUNT_NAME).id

    if get_transaction_by_details(
        source_address=sender_address,
        source_subaddress=sender_subaddress,
        sequence=sequence,
    ):
        log_execution(
            f"Incoming transaction sequence {sequence} already exists. Aborting"
        )
        return

    tx = add_transaction(
        amount=amount,
        currency=currency,
        payment_type=TransactionType.EXTERNAL,
        status=TransactionStatus.COMPLETED,
        source_address=sender_address,
        source_subaddress=sender_subaddress,
        destination_id=receiver_id,
        destination_address=receiver_address,
        destination_subaddress=receiver_subaddr,
        sequence=sequence,
        blockchain_version=blockchain_version,
    )

    log_str = "Settled On Chain"
    add_transaction_log(tx.id, log_str)
    log_execution(f"Processed incoming transaction, saving internally as txn {tx.id}")


def send_transaction(
    sender_id: int,
    amount: int,
    currency: LibraCurrency,
    destination_address: str,
    destination_subaddress: Optional[str] = None,
    payment_type: Optional[TransactionType] = None,
) -> Optional[Transaction]:
    log_execution(
        f"transfer from sender {sender_id} to receiver ({destination_subaddress} {destination_address})"
    )

    if account_service.is_own_address(
        sender_id=sender_id,
        receiver_vasp=destination_address,
        receiver_subaddress=destination_subaddress,
    ):
        raise SelfAsDestinationError(
            "It is not possible to send transaction to your own wallet."
        )

    if not risk_check(sender_id, amount):
        raise RiskCheckError(
            f"Transaction amount is above amount threshold of {TX_AMOUNT_THRESHOLD / 1_000_000}{currency.value}. "
            f"In this case off-chain KYC validation is necessary, which has not been implemented yet."
        )

    if destination_subaddress is None:
        return _unhosted_wallet_transfer(
            sender_id=sender_id, destination_address=destination_address
        )

    if account_service.is_in_wallet(destination_subaddress, destination_address):
        return _send_transaction_internal(
            sender_id=sender_id,
            destination_subaddress=destination_subaddress,
            payment_type=payment_type,
            amount=amount,
            currency=currency,
        )
    else:
        return _send_transaction_external(
            sender_id=sender_id,
            destination_address=destination_address,
            destination_subaddress=destination_subaddress,
            payment_type=payment_type,
            amount=amount,
            currency=currency,
        )


def _unhosted_wallet_transfer(sender_id, destination_address):
    # TODO handle unhosted wallet transfer
    log_execution(
        f"transfer to unhosted wallet transfer from {sender_id} to receiver {destination_address}"
    )
    return None


def _send_transaction_external(
    sender_id,
    destination_address,
    destination_subaddress,
    payment_type,
    amount,
    currency,
) -> Optional[Transaction]:
    log_execution(
        f"external transfer from {sender_id} to receiver {destination_address}, "
        f"receiver subaddress {destination_subaddress}"
    )
    payment_type = TransactionType.EXTERNAL if payment_type is None else payment_type
    return external_transaction(
        sender_id=sender_id,
        receiver_address=destination_address,
        receiver_subaddress=destination_subaddress,
        amount=amount,
        currency=currency,
        payment_type=payment_type,
    )


def _send_transaction_internal(
    sender_id, destination_subaddress, payment_type, amount, currency
) -> Optional[Transaction]:
    log_execution(
        f"internal transfer from {sender_id} to receiver {destination_subaddress}"
    )
    receiver_id = get_account_id_from_subaddr(subaddr=destination_subaddress)
    payment_type = TransactionType.INTERNAL if payment_type is None else payment_type

    return internal_transaction(
        sender_id=sender_id,
        receiver_id=receiver_id,
        amount=amount,
        currency=currency,
        payment_type=payment_type,
    )


def update_transaction(
    transaction_id: int,
    status: Optional[TransactionStatus] = None,
    sequence: Optional[int] = None,
    blockchain_tx_version: Optional[int] = None,
) -> None:
    storage.update_transaction(
        transaction_id=transaction_id,
        sequence=sequence,
        status=status,
        blockchain_version=blockchain_tx_version,
    )


def get_transaction(
    transaction_id: Optional[int] = None, blockchain_version: Optional[int] = None
) -> Transaction:
    if transaction_id:
        return storage.get_transaction(transaction_id)
    if blockchain_version:
        return storage.get_transaction_by_blockchain_version(blockchain_version)


def get_transaction_direction(
    account_id: int, transaction: Transaction
) -> TransactionDirection:
    if transaction.destination_id == account_id:
        return TransactionDirection.RECEIVED

    if transaction.source_id == account_id:
        return TransactionDirection.SENT

    raise LookupError("Couldn't determine transaction direction")


def validate_balance(sender_id: int, amount: int, currency: LibraCurrency) -> bool:
    account_balance = account_service.get_account_balance(account_id=sender_id)
    return amount <= account_balance.total[currency]


def internal_transaction(
    sender_id: int,
    receiver_id: int,
    amount: int,
    currency: LibraCurrency,
    payment_type: TransactionType,
) -> Transaction:
    """Transfer transaction between accounts in the LRW internal ledger."""

    log_execution("Enter internal_transaction")

    if not validate_balance(sender_id, amount, currency):
        raise BalanceError("Balance is less than amount needed")

    sender_subaddress = account_service.generate_new_subaddress(sender_id)
    receiver_subaddress = account_service.generate_new_subaddress(receiver_id)
    internal_vasp_address = OnchainWallet().vasp_address

    transaction = add_transaction(
        amount=amount,
        currency=currency,
        payment_type=payment_type,
        status=TransactionStatus.COMPLETED,
        source_id=sender_id,
        source_address=internal_vasp_address,
        source_subaddress=sender_subaddress,
        destination_id=receiver_id,
        destination_address=internal_vasp_address,
        destination_subaddress=receiver_subaddress,
    )

    log_execution(
        f"Transfer from {sender_id} to {receiver_id} started with transaction id {transaction.id}"
    )
    add_transaction_log(transaction.id, "Transfer completed")
    return transaction


def external_transaction(
    sender_id: int,
    receiver_address: str,
    receiver_subaddress: str,
    amount: int,
    currency: LibraCurrency,
    payment_type: TransactionType,
) -> Transaction:
    if not validate_balance(sender_id, amount, currency):
        raise BalanceError("Balance is less than amount needed")

    sender_subaddress = account_service.generate_new_subaddress(account_id=sender_id)

    transaction = add_transaction(
        amount=amount,
        currency=currency,
        payment_type=payment_type,
        status=TransactionStatus.PENDING,
        source_id=sender_id,
        source_address=OnchainWallet().vasp_address,
        source_subaddress=sender_subaddress,
        destination_id=None,
        destination_address=receiver_address,
        destination_subaddress=receiver_subaddress,
    )

    if services.run_bg_tasks():
        from ..background_tasks.background import async_external_transaction

        async_external_transaction.send(transaction.id)
    else:
        submit_onchain(transaction_id=transaction.id)

    return transaction


def submit_onchain(transaction_id: int) -> None:
    transaction = get_transaction(transaction_id)
    if transaction.status == TransactionStatus.PENDING:
        try:
            libra_currency = LibraCurrency[transaction.currency]

            blockchain_tx_version, tx_sequence = OnchainWallet().send_transaction(
                currency=libra_currency,
                amount=transaction.amount,
                dest_vasp_address=transaction.destination_address,
                dest_sub_address=transaction.destination_subaddress,
                source_subaddr=transaction.source_subaddress,
            )

            update_transaction(
                transaction_id=transaction_id,
                status=TransactionStatus.COMPLETED,
                sequence=tx_sequence,
                blockchain_tx_version=blockchain_tx_version,
            )
            add_transaction_log(transaction_id, "On Chain Transfer Complete")
            log_execution("On Chain Transfer Complete")
        except Exception as e:
            print("Error in _async_start_onchain_transfer: ", e)
            add_transaction_log(transaction_id, "On Chain Transfer Failed")
            log_execution("On Chain Transfer Failed")
            update_transaction(
                transaction_id=transaction_id, status=TransactionStatus.CANCELED
            )


def validate_account_seq(account, transaction_id):
    api = LibraNetwork()
    ar = api.getAccount(account)
    if ar:
        return ar.sequence

    add_transaction_log(
        transaction_id, "On Chain Transfer Failed, account does not exist"
    )
    log_execution("On Chain Transfer Failed, account does not exist")
    update_transaction(transaction_id=transaction_id, status=TransactionStatus.CANCELED)
    raise Exception("Account doesn't exist!")


def wait_for_account_seq(addr_hex: str, seq: int) -> AccountResource:
    num_tries = 0
    log_execution(f"Waiting for {addr_hex} seq {seq}")
    api = LibraNetwork()
    while num_tries < 1000:
        account_resource = api.getAccount(addr_hex)
        if account_resource is not None and account_resource.sequence >= seq:
            return account_resource
        sleep(1)
        num_tries += 1
    raise Exception("Wait for account sequence timed out!")


def get_total_balance() -> Balance:
    credits = get_total_currency_credits()
    debits = get_total_currency_debits()

    balance = Balance()
    for credit in credits:
        if credit.status == TransactionStatus.COMPLETED:
            balance.total[credit.currency] += credit.amount

    for debit in debits:
        if debit.status == TransactionStatus.PENDING:
            balance.frozen[debit.currency] += debit.amount
        if debit.status != TransactionStatus.CANCELED:
            balance.total[debit.currency] -= debit.amount

    return balance