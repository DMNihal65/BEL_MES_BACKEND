# quality_crud.py
from typing import Optional, List
from pony.orm import db_session, commit, select
import json
from fastapi import APIRouter, HTTPException, Depends, Path, Query

from ..core.security import get_current_user
from ..models import Order, Operation, User
from ..models.quality import MasterBoc, StageInspection
from ..schemas.quality_schema import MasterBocCreate, MasterBocResponse, StageInspectionResponse, StageInspectionCreate, \
    QualityInspectionResponse, OrderInfo, StageInspectionDetail, DetailedQualityInspectionResponse, \
 StageInspectionWithOperator, OperatorInfo, OperationGroup

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
    def get_by_order_and_op_no(order_id: int, op_no: int) -> List[MasterBocResponse]:
        """Get all Master BOCs for an order and specific operation number"""
        master_bocs = select(m for m in MasterBoc if m.order_id == order_id and m.op_no == op_no)[:]
        return [MasterBocResponse.from_orm(m) for m in master_bocs]


class StageInspectionCRUD:
    @staticmethod
    @db_session
    def create_stage_inspection(data: StageInspectionCreate) -> StageInspectionResponse:
        """Create a new Stage Inspection entry"""
        try:
            # Create new instance
            stage_inspection = StageInspection(
                op_id=data.op_id,
                nominal_value=data.nominal_value,
                uppertol=data.uppertol,
                lowertol=data.lowertol,
                zone=data.zone,
                dimension_type=data.dimension_type,
                measured_1=data.measured_1,
                measured_2=data.measured_2,
                measured_3=data.measured_3,
                measured_mean=data.measured_mean,
                measured_instrument=data.measured_instrument,
                op_no=data.op_no,
                order_id=data.order_id
            )
            commit()

            return StageInspectionResponse.from_orm(stage_inspection)
        except Exception as e:
            raise ValueError(f"Failed to create Stage Inspection: {str(e)}")


class QualityInspectionCRUD:
    @staticmethod
    @db_session
    def get_quality_inspection_data(order_id: int) -> QualityInspectionResponse:
        """Get comprehensive quality inspection data for an order"""
        # Get order information
        order = Order.get(id=order_id)
        if not order:
            raise ValueError(f"Order with ID {order_id} not found")

        # Get all operations for this order
        operations = select(op for op in Operation if op.order.id == order_id)[:]

        # Get all stage inspections for this order
        stage_inspections = select(si for si in StageInspection
                                   if si.order_id == order_id)[:]

        # Prepare response data
        order_info = OrderInfo(
            order_id=order.id,
            production_order=order.production_order,
            part_number=order.part_number
        )

        inspections = [
            StageInspectionDetail(
                id=si.id,
                op_id=si.op_id,
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
                op_no=si.op_no,
                order_id=si.order_id,
                created_at=si.created_at
            ) for si in stage_inspections
        ]

        return QualityInspectionResponse(
            order_info=order_info,
            inspections=inspections
        )


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