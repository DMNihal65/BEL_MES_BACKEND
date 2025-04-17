# quality_crud.py
from typing import Optional, List
from pony.orm import db_session, commit, select, distinct
import json
from fastapi import APIRouter, HTTPException, Depends, Path, Query

from app.models import Operation, Order, User
from app.models.quality import MasterBoc, StageInspection
from app.schemas.quality import MasterBocCreate, MasterBocResponse, StageInspectionResponse, StageInspectionCreate, \
    QualityInspectionResponse, OrderInfo, StageInspectionDetail, DetailedQualityInspectionResponse, \
    StageInspectionWithOperator, OperatorInfo, OperationGroup, OrderIPIDResponse, MasterBocIPIDInfo, OperationIPIDGroup, \
    IPIDInfo

router = APIRouter()


class MasterBocCRUD:
    @staticmethod
    @db_session
    def create_master_boc(data: MasterBocCreate) -> MasterBocResponse:
        """Create a new Master BOC entry"""
        try:
            # Convert to database format
            db_data = data.to_db_dict()

            # Create new instance
            master_boc = MasterBoc(**db_data)
            commit()

            # Convert to response model
            return MasterBocResponse.from_orm(master_boc)
        except Exception as e:
            raise ValueError(f"Failed to create Master BOC: {str(e)}")

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
        query = select(m for m in MasterBoc
                       if m.order_id == order_id and m.op_no == op_no)

        # Add measurement instruments filter if provided
        if measurement_instruments:
            query = query.filter(lambda m: m.measured_instrument in measurement_instruments)

        master_bocs = query.order_by(MasterBoc.id)[:]
        return [MasterBocResponse.from_orm(m) for m in master_bocs]

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
        master_bocs = select(m for m in MasterBoc if m.order_id == order_id).order_by(
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
                # Verify that the first quantity for this order & op_no exists and is marked as done
                first_quantity = select(si for si in StageInspection
                                        if si.order_id == data.order_id
                                        and si.op_no == data.op_no
                                        and si.quantity_no == 1).first()

                if not first_quantity:
                    raise ValueError(
                        f"Cannot add quantity {data.quantity_no} because quantity 1 does not exist for order {data.order_id}, operation {data.op_no}")

                if not first_quantity.is_done:
                    raise ValueError(
                        f"Cannot add quantity {data.quantity_no} because quantity 1 for order {data.order_id}, operation {data.op_no} is not marked as done")

                # Check if previous quantity exists and is marked as done
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

            return StageInspectionResponse.from_orm(stage_inspection)
        except Exception as e:
            raise ValueError(f"Failed to create Stage Inspection: {str(e)}")

    @staticmethod
    @db_session
    def update_inspection_status(inspection_id: int, is_done: bool) -> StageInspectionResponse:
        """Update the is_done status of a stage inspection"""
        try:
            inspection = StageInspection.get(id=inspection_id)
            if not inspection:
                raise ValueError(f"Stage inspection with ID {inspection_id} not found")

            inspection.is_done = is_done
            commit()

            return StageInspectionResponse.from_orm(inspection)
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