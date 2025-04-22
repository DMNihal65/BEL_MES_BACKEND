# quality_crud.py
from typing import Optional, List
from pony.orm import db_session, commit, select, distinct
import json
from fastapi import APIRouter, HTTPException, Depends, Path, Query
from datetime import datetime

from app.models import Operation, Order, User
from app.models.document_management_v2 import DocumentV2
from app.models.quality import MasterBoc, StageInspection, FTP
from app.schemas.quality import MasterBocCreate, MasterBocResponse, StageInspectionResponse, StageInspectionCreate, \
    QualityInspectionResponse, OrderInfo, StageInspectionDetail, DetailedQualityInspectionResponse, \
    StageInspectionWithOperator, OperatorInfo, OperationGroup, OrderIPIDResponse, MasterBocIPIDInfo, OperationIPIDGroup, \
    IPIDInfo, FTPResponse

router = APIRouter()


class MasterBocCRUD:
    @staticmethod
    @db_session
    def create_master_boc(data: MasterBocCreate) -> MasterBocResponse:
        """Create a new Master BOC entry or update existing one based on bbox"""
        try:
            # Verify that Order and Document exist
            order = Order.get(id=data.order_id)
            if not order:
                raise ValueError(f"Order with ID {data.order_id} not found")

            document = DocumentV2.get(id=data.document_id)
            if not document:
                raise ValueError(f"Document with ID {data.document_id} not found")

            # Convert to database format
            db_data = data.to_db_dict()

            # Check if a master_boc with the same bbox exists
            existing_master_boc = select(m for m in MasterBoc
                                         if m.order.id == data.order_id
                                         and m.op_no == data.op_no
                                         and m.bbox == db_data['bbox']).first()

            if existing_master_boc:
                # Update existing master_boc
                existing_master_boc.document = document
                existing_master_boc.nominal = db_data['nominal']
                existing_master_boc.uppertol = db_data['uppertol']
                existing_master_boc.lowertol = db_data['lowertol']
                existing_master_boc.zone = db_data['zone']
                existing_master_boc.dimension_type = db_data['dimension_type']
                existing_master_boc.measured_instrument = db_data['measured_instrument']
                existing_master_boc.ipid = db_data['ipid']
                master_boc = existing_master_boc
            else:
                # Create new instance with proper relationships
                master_boc = MasterBoc(
                    order=order,
                    document=document,
                    nominal=db_data['nominal'],
                    uppertol=db_data['uppertol'],
                    lowertol=db_data['lowertol'],
                    zone=db_data['zone'],
                    dimension_type=db_data['dimension_type'],
                    measured_instrument=db_data['measured_instrument'],
                    op_no=db_data['op_no'],
                    bbox=db_data['bbox'],
                    ipid=db_data['ipid']
                )

            commit()

            # Convert to response model
            return MasterBocResponse.from_orm(master_boc)
        except ValueError as e:
            raise ValueError(str(e))
        except Exception as e:
            raise ValueError(f"Failed to create/update Master BOC: {str(e)}")

    @staticmethod
    @db_session
    def get_master_boc(id: int) -> Optional[MasterBocResponse]:
        """Get Master BOC by ID"""
        master_boc = MasterBoc.get(id=id)
        if master_boc:
            return MasterBocResponse.from_orm(master_boc)
        return None

    @staticmethod
    @db_session
    def get_by_order_and_op_no(
            order_id: int,
            op_no: int,
            measurement_instruments: Optional[List[str]] = None
    ) -> List[MasterBocResponse]:
        """Get all Master BOCs for an order and specific operation number"""
        try:
            # Verify that Order exists
            order = Order.get(id=order_id)
            if not order:
                raise ValueError(f"Order with ID {order_id} not found")

            query = select(m for m in MasterBoc
                           if m.order.id == order_id and m.op_no == op_no)

            # Add measurement instruments filter if provided
            if measurement_instruments:
                query = query.filter(lambda m: m.measured_instrument in measurement_instruments)

            master_bocs = query.order_by(MasterBoc.id)[:]
            return [MasterBocResponse.from_orm(m) for m in master_bocs]
        except ValueError as e:
            raise ValueError(str(e))
        except Exception as e:
            raise ValueError(f"Failed to get Master BOCs: {str(e)}")

    @staticmethod
    @db_session
    def get_ipids_by_order(order_id: int) -> OrderIPIDResponse:
        """Get all IPIDs for an order, grouped by operation number"""
        # Get order information
        order = Order.get(id=order_id)
        if not order:
            raise ValueError(f"Order with ID {order_id} not found")

        # Get all operations for this order to show even if no master bocs exist
        operations = select(op for op in Operation if op.order.id == order_id).order_by(
            Operation.operation_number)[:]

        if not operations:
            raise ValueError(f"No operations found for order {order_id}")

        # Get all master bocs for this order
        master_bocs = select(m for m in MasterBoc if m.order == order).order_by(
            MasterBoc.op_no)[:]

        # Create operation groups (will be empty if no master bocs found)
        operation_groups = []
        for boc in master_bocs:
            ipid_info = IPIDInfo(
                zone=boc.zone,
                dimension_type=boc.dimension_type,
                nominal=boc.nominal,
                uppertol=boc.uppertol,
                lowertol=boc.lowertol,
                measured_instrument=boc.measured_instrument
            )

            operation_group = OperationIPIDGroup(
                op_no=boc.op_no,
                ipid=boc.ipid,
                details=ipid_info
            )
            operation_groups.append(operation_group)

        # Return response with order info even if no master bocs exist
        return OrderIPIDResponse(
            order_id=order.id,
            production_order=order.production_order,
            part_number=order.part_number,
            operation_groups=operation_groups,  # Will be empty list if no master bocs
            operations=[op.operation_number for op in operations]  # Added operations list
        )

    @staticmethod
    @db_session
    def get_all_measurement_instruments() -> List[str]:
        """Get all unique measurement instruments from master boc table"""
        # Using select to get unique values
        instruments = select(m.measured_instrument for m in MasterBoc)
        # Convert to set to get unique values and then back to sorted list
        unique_instruments = sorted(set(instruments[:]))
        return unique_instruments


class StageInspectionCRUD:
    @staticmethod
    @db_session
    def create_stage_inspection(data: StageInspectionCreate) -> StageInspectionResponse:
        """Create a new Stage Inspection entry with validation for quantity progression"""
        try:
            # Check if this is a subsequent quantity for the same order and operation
            if data.quantity_no is not None and data.quantity_no > 1:
                # Get all master_bocs for this order and operation to find IPIDs
                master_bocs = select(m for m in MasterBoc
                                     if m.order.id == data.order_id
                                     and m.op_no == data.op_no)[:]

                # Check FTP status for all IPIDs
                for master_boc in master_bocs:
                    ftp_status = FTP.get(order_id=data.order_id, ipid=master_boc.ipid)
                    if ftp_status and ftp_status.is_completed:
                        # If FTP is completed for quantity 1, automatically mark this as done
                        data.is_done = True
                        break

                # Verify that the first quantity exists
                first_quantity = select(si for si in StageInspection
                                        if si.order_id == data.order_id
                                        and si.op_no == data.op_no
                                        and si.quantity_no == 1).first()

                if not first_quantity:
                    raise ValueError(
                        f"Cannot add quantity {data.quantity_no} because quantity 1 does not exist for order {data.order_id}, operation {data.op_no}")

                # Check previous quantity
                prev_quantity = select(si for si in StageInspection
                                       if si.order_id == data.order_id
                                       and si.op_no == data.op_no
                                       and si.quantity_no == data.quantity_no - 1).first()

                if prev_quantity and not prev_quantity.is_done:
                    raise ValueError(
                        f"Cannot add quantity {data.quantity_no} because previous quantity is not marked as done")

            # Create new instance
            stage_inspection_data = {
                'op_id': data.op_id,
                'nominal_value': data.nominal_value,
                'uppertol': data.uppertol,
                'lowertol': data.lowertol,
                'zone': data.zone,
                'dimension_type': data.dimension_type,
                'measured_1': data.measured_1,
                'measured_2': data.measured_2,
                'measured_3': data.measured_3,
                'measured_mean': data.measured_mean,
                'measured_instrument': data.measured_instrument,
                'op_no': data.op_no,
                'order_id': data.order_id,
                'is_done': data.is_done,
            }

            # Only add quantity_no if it's provided
            if data.quantity_no is not None:
                stage_inspection_data['quantity_no'] = data.quantity_no

            stage_inspection = StageInspection(**stage_inspection_data)
            commit()

            # After creating stage inspection, update FTP status if this is quantity 1
            if data.quantity_no == 1 and data.is_done:
                # Find all master_bocs for this order and operation
                master_bocs = select(m for m in MasterBoc
                                     if m.order.id == data.order_id
                                     and m.op_no == data.op_no)[:]

                # Update FTP status for each master_boc's IPID
                for master_boc in master_bocs:
                    # Get or create FTP entry
                    ftp = FTP.get(order_id=data.order_id, ipid=master_boc.ipid)
                    if not ftp:
                        ftp = FTP(
                            order_id=data.order_id,
                            ipid=master_boc.ipid,
                            is_completed=data.is_done
                        )
                    else:
                        ftp.is_completed = data.is_done
                        ftp.updated_at = datetime.now()

                # If quantity 1 is marked as done and FTP is completed, mark all existing quantities as done
                if data.is_done:
                    existing_quantities = select(si for si in StageInspection
                                                 if si.order_id == data.order_id
                                                 and si.op_no == data.op_no
                                                 and si.quantity_no is not None
                                                 and si.quantity_no > 1)[:]
                    for qty in existing_quantities:
                        qty.is_done = True

            commit()
            return StageInspectionResponse.from_orm(stage_inspection)

        except Exception as e:
            raise ValueError(f"Failed to create Stage Inspection: {str(e)}")

    @staticmethod
    @db_session
    def update_inspection_status(inspection_id: int, is_done: bool) -> StageInspectionResponse:
        """Update the is_done status of a stage inspection and update related FTP statuses"""
        try:
            # Get the stage inspection
            inspection = StageInspection.get(id=inspection_id)
            if not inspection:
                raise ValueError(f"Stage inspection with ID {inspection_id} not found")

            # Update the inspection status
            inspection.is_done = is_done

            # If this is quantity 1 and it's being marked as done
            if inspection.quantity_no == 1 and is_done:
                # Get all master_bocs for this order and operation
                master_bocs = select(m for m in MasterBoc
                                     if m.order.id == inspection.order_id
                                     and m.op_no == inspection.op_no)[:]

                # Update FTP status for each master_boc's IPID
                for master_boc in master_bocs:
                    # Get or create FTP entry
                    ftp = FTP.get(order_id=inspection.order_id, ipid=master_boc.ipid)
                    if ftp:
                        ftp.is_completed = is_done
                        ftp.updated_at = datetime.now()
                    else:
                        ftp = FTP(
                            order_id=inspection.order_id,
                            ipid=master_boc.ipid,
                            is_completed=is_done
                        )

                # If marking quantity 1 as done, automatically mark all other quantities as done
                if is_done:
                    other_quantities = select(si for si in StageInspection
                                              if si.order_id == inspection.order_id
                                              and si.op_no == inspection.op_no
                                              and si.quantity_no is not None
                                              and si.quantity_no > 1)[:]
                    for qty in other_quantities:
                        qty.is_done = True

            commit()
            return StageInspectionResponse.from_orm(inspection)

        except ValueError as e:
            raise ValueError(str(e))
        except Exception as e:
            raise ValueError(f"Failed to update inspection status: {str(e)}")


class QualityInspectionCRUD:
    @staticmethod
    @db_session
    def get_detailed_inspection_data(order_id: int) -> DetailedQualityInspectionResponse:
        """Get detailed quality inspection data with all operations and their inspections"""
        # Get order information
        order = Order.get(id=order_id)
        if not order:
            raise ValueError(f"Order with ID {order_id} not found")

        # Get all operations for this order
        operations = select(op for op in Operation if op.order.id == order_id).order_by(
            Operation.operation_number)[:]

        if not operations:
            raise ValueError(f"No operations found for order {order_id}")

        # Get all operation numbers
        operation_numbers = [op.operation_number for op in operations]

        inspection_groups = []

        # Process each operation that has inspections
        for op in operations:
            # Get stage inspections for this operation
            stage_inspections = select(si for si in StageInspection
                                       if si.order_id == order_id and
                                       si.op_no == op.operation_number)[:]

            if stage_inspections:  # Only add to inspection_data if there are inspections
                inspection_list = []
                for si in stage_inspections:
                    # Get operator information
                    operator = User.get(id=si.op_id)
                    if operator:
                        operator_info = OperatorInfo(
                            id=operator.id,
                            username=operator.username,
                            email=operator.email
                        )

                        inspection_list.append(
                            StageInspectionWithOperator(
                                id=si.id,
                                nominal_value=si.nominal_value,
                                uppertol=si.uppertol,
                                lowertol=si.lowertol,
                                zone=si.zone,
                                dimension_type=si.dimension_type,
                                measured_1=si.measured_1,
                                measured_2=si.measured_2,
                                measured_3=si.measured_3,
                                measured_mean=si.measured_mean,
                                measured_instrument=si.measured_instrument,
                                is_done=si.is_done,  # Added is_done field
                                quantity_no=si.quantity_no,  # Include quantity_no
                                created_at=si.created_at,
                                operator=operator_info
                            )
                        )

                if inspection_list:
                    inspection_groups.append(
                        OperationGroup(
                            operation_number=op.operation_number,
                            inspections=inspection_list
                        )
                    )

        return DetailedQualityInspectionResponse(
            order_id=order.id,
            production_order=order.production_order,
            part_number=order.part_number,
            operations=operation_numbers,  # All operation numbers
            inspection_data=inspection_groups  # Only operations with inspections
        )


class FTPCRUD:
    @staticmethod
    @db_session
    def update_ftp_status(order_id: int, ipid: str) -> Optional[FTPResponse]:
        """
        Update or create FTP status for a given order_id and ipid.
        Checks all stage inspections related to the ipid and updates status accordingly.
        """
        try:
            # Get the master_boc entry for this ipid
            master_boc = MasterBoc.get(order_id=order_id, ipid=ipid)
            if not master_boc:
                raise ValueError(f"No master_boc found for order_id {order_id} and ipid {ipid}")

            # Get all stage inspections for this order and operation number
            stage_inspections = select(si for si in StageInspection
                                       if si.order_id == order_id
                                       and si.op_no == master_boc.op_no)[:]

            # If there are no stage inspections, consider it not completed
            if not stage_inspections:
                is_completed = False
            else:
                # Check if all stage inspections are marked as done
                is_completed = all(si.is_done for si in stage_inspections)

            # Get or create FTP entry
            ftp = FTP.get(order_id=order_id, ipid=ipid)
            if not ftp:
                ftp = FTP(
                    order_id=order_id,
                    ipid=ipid,
                    is_completed=is_completed
                )
            else:
                ftp.is_completed = is_completed
                ftp.updated_at = datetime.now()

            commit()
            return FTPResponse.from_orm(ftp)

        except Exception as e:
            raise ValueError(f"Failed to update FTP status: {str(e)}")

    @staticmethod
    @db_session
    def get_ftp_status(order_id: int, ipid: str) -> Optional[FTPResponse]:
        """Get FTP status for a given order_id and ipid"""
        try:
            ftp = FTP.get(order_id=order_id, ipid=ipid)
            if ftp:
                return FTPResponse.from_orm(ftp)
            return None
        except Exception as e:
            raise ValueError(f"Failed to get FTP status: {str(e)}")

    @staticmethod
    @db_session
    def get_all_ftp_by_order(order_id: int) -> List[FTPResponse]:
        """Get all FTP entries for a given order"""
        try:
            ftps = select(f for f in FTP if f.order_id == order_id)[:]
            return [FTPResponse.from_orm(f) for f in ftps]
        except Exception as e:
            raise ValueError(f"Failed to get FTP entries: {str(e)}")