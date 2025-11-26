# quality_crud.py
import logging
from typing import Optional, List
from pony.orm import db_session, commit, select, distinct, desc
import json
from fastapi import APIRouter, HTTPException, Depends, Path, Query
from datetime import datetime

from app.models import Operation, Order, User
from app.models.document_management_v2 import DocumentV2, DocumentTypeV2
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
            # Get the most recent order with this part_number
            order = select(o for o in Order if o.part_number == data.part_number).order_by(lambda o: desc(o.id)).first()
            if not order:
                raise ValueError(f"Order with part_number {data.part_number} not found")

            # Validate bbox has exactly 8 values
            if len(data.bbox) != 8:
                raise ValueError("bbox must contain exactly 8 values [x1, y1, x2, y2, x3, y3, x4, y4]")

            # Convert to database format
            db_data = data.to_db_dict()

            # Check if a master_boc with the same bbox exists for this part number and operation
            existing_master_boc = select(m for m in MasterBoc
                                         if m.part_number == data.part_number
                                         and m.op_no == data.op_no
                                         and m.bbox == db_data['bbox']).first()

            if existing_master_boc:
                # Update existing master_boc
                existing_master_boc.part_number = data.part_number
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
                    part_number=data.part_number,
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
    def get_by_part_number_and_op_no(
            part_number: str,
            op_no: int,
            measurement_instruments: Optional[List[str]] = None
    ) -> List[MasterBocResponse]:
        """Get all Master BOCs for a part number and specific operation number"""
        try:
            # Get all orders with this part_number
            orders = select(o for o in Order if o.part_number == part_number)[:]
            if not orders:
                raise ValueError(f"No orders found with part_number {part_number}")

            # Query MasterBocs where the linked Order's part_number matches
            query = select(m for m in MasterBoc
                           if m.part_number == part_number and m.op_no == op_no)

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
    def delete_master_boc(id: int) -> bool:
        """
        Delete a Master BOC entry by ID

        Args:
            id: The ID of the Master BOC to delete

        Returns:
            bool: True if deleted successfully, False if not found

        Raises:
            ValueError: If deletion fails due to constraints or other issues
        """
        try:
            master_boc = MasterBoc.get(id=id)
            if not master_boc:
                return False

            # Check if there are any related stage inspections that might depend on this
            # You might want to add validation here based on your business logic

            # Delete the record
            master_boc.delete()
            commit()
            return True

        except Exception as e:
            raise ValueError(f"Failed to delete Master BOC with ID {id}: {str(e)}")

    @staticmethod
    @db_session
    def get_ipids_by_order_and_part_number(order_id: int, part_number: str) -> OrderIPIDResponse:
        """Get all IPIDs for an order and part number combination"""
        # Get order information
        order = Order.get(id=order_id)
        if not order:
            raise ValueError(f"Order with ID {order_id} not found")

        # Get all operations for this order to show even if no master bocs exist
        operations = select(op for op in Operation if op.order.id == order_id).order_by(
            Operation.operation_number)[:]

        if not operations:
            raise ValueError(f"No operations found for order {order_id}")

        # Get all master bocs for this part number
        master_bocs = select(m for m in MasterBoc if m.part_number == part_number).order_by(
            MasterBoc.op_no)[:]

        # Create operation groups (will be empty if no master bocs found)
        operation_groups = []
        for boc in master_bocs:
            ipid_info = IPIDInfo(
                id=boc.id,  # <-- include ID here
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
            part_number=part_number,  # Use the provided part number
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
        logging.basicConfig(level=logging.INFO)
        logger = logging.getLogger(__name__)

        try:
            logger.info(
                f"Starting stage inspection creation for order_id: {data.order_id}, "
                f"op_no: {data.op_no}, quantity: {data.quantity_no}"
            )

            # Always fetch the order first
            order = Order.get(id=data.order_id)
            if not order:
                logger.error(f"Order with ID {data.order_id} not found.")
                raise ValueError(f"Order with ID {data.order_id} not found")

            part_number = order.part_number
            logger.info(
                f"Successfully fetched order {order.id} with part_number {part_number}"
            )

            # For quantity > 1, verify that quantity 1 exists and FTP has been approved
            if data.quantity_no is not None and data.quantity_no > 1:
                # Verify that the first quantity exists
                first_quantity = select(
                    si for si in StageInspection
                    if si.order_id == data.order_id
                    and si.op_no == data.op_no
                    and si.quantity_no == 1
                ).first()

                if not first_quantity:
                    raise ValueError(
                        f"Cannot add quantity {data.quantity_no} because quantity 1 does not exist "
                        f"for order {data.order_id}, operation {data.op_no}"
                    )

                # Check if FTP is approved for this order and operation
                master_bocs = select(
                    m for m in MasterBoc
                    if m.part_number == part_number
                    and m.op_no == data.op_no
                )[:]

                all_ftp_completed = True
                for master_boc in master_bocs:
                    ftp_status = FTP.get(order_id=data.order_id, ipid=master_boc.ipid)
                    if not ftp_status or not ftp_status.is_completed:
                        all_ftp_completed = False
                        break

                if not all_ftp_completed:
                    raise ValueError(
                        f"Cannot add quantity {data.quantity_no} because FTP approval for quantity 1 "
                        f"is still pending for order {data.order_id}, operation {data.op_no}"
                    )

            # Check if a stage inspection with the same key data exists
            bbox_str = json.dumps(data.bbox) if data.bbox else None

            existing_inspection = select(
                si for si in StageInspection
                if si.order_id == data.order_id
                and si.op_no == data.op_no
                and si.quantity_no == data.quantity_no
                and si.zone == data.zone
                and si.dimension_type == data.dimension_type
                and si.nominal_value == data.nominal_value
                and (
                    (si.bbox == bbox_str)
                    if bbox_str is not None
                    else (si.bbox is None)
                )
            ).first()

            # Convert to database format
            stage_inspection_data = data.to_db_dict()

            # Remove quantity_no if None (avoid setting null explicitly)
            if stage_inspection_data.get("quantity_no") is None:
                stage_inspection_data.pop("quantity_no", None)

            if existing_inspection:
                # Update existing inspection
                for key, value in stage_inspection_data.items():
                    setattr(existing_inspection, key, value)
                stage_inspection = existing_inspection
            else:
                # Create new instance
                stage_inspection = StageInspection(**stage_inspection_data)

            commit()

            # After creating/updating stage inspection, create or update FTP status
            master_bocs = select(
                m for m in MasterBoc
                if m.part_number == part_number
                and m.op_no == data.op_no
            )[:]

            for master_boc in master_bocs:
                ftp = FTP.get(order_id=data.order_id, ipid=master_boc.ipid)
                if not ftp:
                    ftp = FTP(
                        order_id=data.order_id,
                        ipid=master_boc.ipid,
                        is_completed=False,  # Initially set to false
                        Status="NA"  # Initially set as Not Approved
                    )
                # Do not overwrite existing FTP entries

            commit()
            return StageInspectionResponse.from_orm(stage_inspection)

        except NameError as ne:
            logger.error(f"NameError in create_stage_inspection: {ne}", exc_info=True)
            raise ValueError(f"Failed to create Stage Inspection: {ne}")
        except Exception as e:
            logger.error(f"An unexpected error occurred in create_stage_inspection: {e}", exc_info=True)
            raise ValueError(f"Failed to create Stage Inspection: {str(e)}")

    @staticmethod
    @db_session
    def update_inspection_status(inspection_id: int, is_completed: bool) -> StageInspectionResponse:
        """Update the related FTP statuses for a stage inspection"""
        try:
            # Get the stage inspection
            inspection = StageInspection.get(id=inspection_id)
            if not inspection:
                raise ValueError(f"Stage inspection with ID {inspection_id} not found")

            # If this is quantity 1
            if inspection.quantity_no == 1:
                # Get all master_bocs for this order and operation
                order = Order.get(id=inspection.order_id)
                if not order:
                    raise ValueError(f"Order with ID {inspection.order_id} not found")
                    
                part_number = order.part_number
                master_bocs = select(m for m in MasterBoc
                                     if m.part_number == part_number
                                     and m.op_no == inspection.op_no)[:]

                # Update FTP status for each master_boc's IPID
                for master_boc in master_bocs:
                    # Get or create FTP entry
                    ftp = FTP.get(order_id=inspection.order_id, ipid=master_boc.ipid)
                    if ftp:
                        ftp.is_completed = is_completed
                        ftp.updated_at = datetime.now()
                    else:
                        ftp = FTP(
                            order_id=inspection.order_id,
                            ipid=master_boc.ipid,
                            is_completed=is_completed
                        )

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
                                used_inst=si.used_inst,
                                quantity_no=si.quantity_no,
                                bbox=json.loads(si.bbox) if si.bbox else None,
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
    def update_ftp_status(order_id: int, ipid: str, is_completed: bool, status: str) -> Optional[FTPResponse]:
        """
        Update or create FTP status for a given order_id and ipid.
        Sets is_completed and status based on user input.
        """
        try:
            order = Order.get(id=order_id)
            if not order:
                raise ValueError(f"Order with ID {order_id} not found")

            part_number = order.part_number
            master_boc = select(m for m in MasterBoc if m.part_number == part_number and m.ipid == ipid).first()
            if not master_boc:
                raise ValueError(f"No master_boc found for order_id {order_id} and ipid {ipid}")

            # Get or create FTP entry
            ftp = FTP.get(order_id=order_id, ipid=ipid)
            if not ftp:
                ftp = FTP(
                    order_id=order_id,
                    ipid=ipid,
                    is_completed=is_completed,
                    Status=status  # Note: using Status with capital S as per your model
                )
            else:
                ftp.is_completed = is_completed
                ftp.Status = status  # Note: using Status with capital S as per your model
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